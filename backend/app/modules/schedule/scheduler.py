from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core.config import settings
from app.core.tz import scheduler_timezone, zone
from app.db.base import utcnow
from app.db.session import session_scope
from app.jobs.runner import log_scheduler_skip, recover_interrupted_logs, run_job
from app.modules.schedule.locks import purge_expired_locks
from app.modules.schedule.models import Job

logger = logging.getLogger("fastcms.scheduler")

SKIP_EVENT_MASK = EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES
SKIP_LOG_WINDOW_SECONDS = 60
SKIP_LOG_MAX_JOBS = 1024


def _consume_task(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("failed to record the scheduler skip: %s", exc)


def job_zone(job: Job) -> ZoneInfo:
    """任务未指定时区时跟随调度时区，调度时区未配置时跟随系统时区。"""
    return zone(job.timezone or scheduler_timezone())


def build_trigger(job: Job, anchor: datetime | None = None) -> BaseTrigger:
    """Build a trigger. ``anchor`` pins the interval phase so a reload cannot
    restart the countdown from ``now`` (APScheduler defaults to now + interval)."""
    timezone = job_zone(job)
    if job.trigger_type == "cron":
        if not job.cron_expr:
            raise ValueError("cron 表达式为空")
        return CronTrigger.from_crontab(job.cron_expr.strip(), timezone=timezone)
    if job.trigger_type == "interval":
        if not job.interval_seconds or job.interval_seconds < 10:
            raise ValueError("间隔任务秒数必须不小于 10")
        return IntervalTrigger(
            seconds=job.interval_seconds, timezone=timezone, start_date=anchor
        )
    if job.trigger_type == "date":
        if job.run_at is None:
            raise ValueError("单次任务必须设置执行时间")
        return DateTrigger(run_date=job.run_at, timezone=timezone)
    raise ValueError(f"未知触发器类型：{job.trigger_type}")


def compute_next_run(job: Job) -> datetime | None:
    trigger = build_trigger(job)
    now = utcnow().astimezone(job_zone(job))
    return trigger.get_next_fire_time(None, now)


def preview_next_runs(job: Job, count: int = 5) -> list[datetime]:
    trigger = build_trigger(job)
    timezone = job_zone(job)
    previous: datetime | None = None
    now = utcnow().astimezone(timezone)
    result: list[datetime] = []
    for _ in range(max(1, min(count, 10))):
        next_time = trigger.get_next_fire_time(previous, now if previous is None else previous)
        if next_time is None:
            break
        result.append(next_time)
        previous = next_time
    return result


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self._recent_skips: dict[tuple[int, str], datetime] = {}

    @property
    def running(self) -> bool:
        return bool(self.scheduler and self.scheduler.running)

    async def start(self) -> None:
        if self.running:
            return
        await recover_interrupted_logs()
        self.scheduler = AsyncIOScheduler(timezone=zone(scheduler_timezone()))
        self.scheduler.add_listener(self._on_event, SKIP_EVENT_MASK)
        self.scheduler.start()
        await self.sync_all()
        logger.info(
            "scheduler started mode=%s jobs=%s timezone=%s",
            settings.scheduler_mode,
            len(self.scheduler.get_jobs()),
            self.scheduler.timezone,
        )

    async def resync_timezone(self) -> None:
        """系统时区或调度时区变了：调度器实例的时区不可改，只能整体重建一次。"""
        if not self.running:
            return
        assert self.scheduler is not None
        if self.scheduler.timezone == zone(scheduler_timezone()):
            await self.sync_all()
            return
        self.scheduler.shutdown(wait=False)
        await self.start()

    async def shutdown(self) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.scheduler = None

    async def sync_all(self) -> None:
        if not self.running:
            return
        assert self.scheduler is not None
        enabled_ids: set[int] = set()
        async with session_scope() as db:
            jobs = (await db.execute(select(Job))).scalars().all()
            for job in jobs:
                if job.status != "enabled":
                    if job.next_run_at is not None:
                        job.next_run_at = None
                    continue
                try:
                    trigger = build_trigger(job, anchor=job.next_run_at)
                    signature = str(trigger)
                except Exception as exc:  # invalid persisted configuration must not stop the API
                    logger.warning("failed to schedule job %s: %s", job.code, exc)
                    job.next_run_at = None
                    self._remove(job.id)
                    continue
                enabled_ids.add(job.id)
                existing = self.scheduler.get_job(str(job.id))
                if (
                    existing is not None
                    and existing.name == job.name
                    and str(existing.trigger) == signature
                    and str(existing.trigger.timezone) == str(trigger.timezone)
                ):
                    next_run = existing.next_run_time
                else:
                    self._remove(job.id)
                    try:
                        next_run = self._add(job, trigger)
                    except Exception as exc:
                        logger.warning("failed to schedule job %s: %s", job.code, exc)
                        next_run = None
                if job.next_run_at != next_run:
                    job.next_run_at = next_run
            for scheduled in self.scheduler.get_jobs():
                try:
                    job_id = int(scheduled.id)
                except ValueError:
                    continue
                if job_id not in enabled_ids:
                    self._remove(job_id)
        try:
            await purge_expired_locks()
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to purge expired job locks: %s", exc)

    def _add(
        self,
        job: Job,
        trigger: BaseTrigger | None = None,
        anchor: datetime | None = None,
    ) -> datetime | None:
        assert self.scheduler is not None
        trigger = trigger or build_trigger(job, anchor)
        grace = settings.scheduler_misfire_grace_seconds
        scheduled = self.scheduler.add_job(
            run_job,
            trigger=trigger,
            args=[job.id, "scheduler"],
            id=str(job.id),
            name=job.name,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=grace if grace > 0 else None,
        )
        return scheduled.next_run_time

    def _remove(self, job_id: int) -> None:
        if self.scheduler is None:
            return
        try:
            self.scheduler.remove_job(str(job_id))
        except JobLookupError:
            # 任务本来就没排上（新建/已停用）属于正常情况
            pass
        except Exception as exc:  # noqa: BLE001
            # 其它异常说明调度表可能仍留着旧触发器，静默会让「已暂停」的任务继续跑
            logger.warning("failed to unschedule job %s: %s", job_id, exc)

    def _should_log_skip(self, job_id: int, reason: str) -> bool:
        now = utcnow()
        if len(self._recent_skips) > SKIP_LOG_MAX_JOBS:
            self._recent_skips.clear()
        key = (job_id, reason)
        previous = self._recent_skips.get(key)
        if previous is not None and (now - previous).total_seconds() < SKIP_LOG_WINDOW_SECONDS:
            return False
        self._recent_skips[key] = now
        return True

    def _on_event(self, event: JobExecutionEvent | JobSubmissionEvent) -> None:
        try:
            self._record_skip(event)
        except Exception as exc:  # noqa: BLE001 - logging a skip must never break scheduling
            logger.warning("failed to handle scheduler event: %s", exc)

    def _record_skip(self, event: JobExecutionEvent | JobSubmissionEvent) -> None:
        if event.code == EVENT_JOB_MISSED:
            reason = "错过执行时间（调度进程繁忙）"
        elif event.code == EVENT_JOB_MAX_INSTANCES:
            reason = "上一轮任务尚未结束，本次跳过"
        else:
            return
        try:
            job_id = int(event.job_id)
        except (TypeError, ValueError):
            return
        if not self._should_log_skip(job_id, reason):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        scheduled_at = event.run_times[-1] if event.run_times else None
        task = loop.create_task(log_scheduler_skip(job_id, scheduled_at, reason))
        task.add_done_callback(_consume_task)

    async def reload_job(self, job_id: int) -> None:
        if not self.running:
            return
        self._remove(job_id)
        async with session_scope() as db:
            job = await db.get(Job, job_id)
            if job is None or job.status != "enabled":
                return
            try:
                job.next_run_at = self._add(job, anchor=job.next_run_at)
            except Exception as exc:
                logger.warning("failed to reschedule job %s: %s", job.code, exc)
                job.next_run_at = None

    async def remove_job(self, job_id: int) -> None:
        if not self.running:
            return
        self._remove(job_id)

    async def refresh_job_state(self, job_id: int) -> None:
        async with session_scope() as db:
            job = await db.get(Job, job_id)
            if job is None:
                return
            if not self.running:
                # Another process (standalone scheduler) owns next_run_at in this mode.
                return
            if job.status != "enabled":
                job.next_run_at = None
                return
            assert self.scheduler is not None
            scheduled = self.scheduler.get_job(str(job_id))
            job.next_run_at = scheduled.next_run_time if scheduled else None


scheduler_service = SchedulerService()
