from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
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
from app.modules.ops.schedule.locks import purge_expired_locks
from app.modules.ops.schedule.models import Job

logger = logging.getLogger("fastcms.scheduler")

SKIP_EVENT_MASK = EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES
SKIP_LOG_WINDOW_SECONDS = 60
SKIP_LOG_MAX_JOBS = 1024
# 停机等待在途任务的上限：宁可取消并记 warning，也不能让 lifespan 永远不返回
SHUTDOWN_WAIT_SECONDS = 10.0


# create_task 返回的 Task 只有弱引用：只挂 done_callback 时，CPython 可能在
# 协程跑完之前就把它回收掉（事件循环不持有强引用），skipped 日志会静默丢失。
# 用模块级集合持有强引用，完成后再移除。
_skip_tasks: set[asyncio.Task[None]] = set()


def _consume_task(task: asyncio.Task[None]) -> None:
    _skip_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("failed to record the scheduler skip: %s", exc)


def _inflight_job_tasks(scheduler: AsyncIOScheduler) -> set[asyncio.Future[Any]]:
    """收集调度器 executor 里还没跑完的任务。

    APScheduler 3.x 的 ``AsyncIOExecutor.shutdown()`` **无论 wait 取值都会**
    ``cancel()`` 掉全部 ``_pending_futures``（其源码明确注释 wait=True 无法在
    同步方法里兑现）。所以调用 ``scheduler.shutdown()`` 之前必须先 pause + 等
    在途任务收尾，否则任务正在写 JobLog / 释放租约时连接就被 dispose 收走了。
    这里读的是 3.x 的私有字段，用 getattr 兜住版本差异，拿不到就退化为不等待。
    """
    tasks: set[asyncio.Future[Any]] = set()
    for executor in getattr(scheduler, "_executors", {}).values():
        for future in tuple(getattr(executor, "_pending_futures", ())):
            if not future.done():
                tasks.add(future)
    return tasks


def _event_scheduled_at(event: JobExecutionEvent | JobSubmissionEvent) -> datetime | None:
    """取事件的计划执行时间。

    APScheduler 3.x 的两种事件字段不同：``JobSubmissionEvent``（MISSED /
    MAX_INSTANCES 走这一类）只有 ``scheduled_run_times`` 列表，
    ``JobExecutionEvent`` 只有单数 ``scheduled_run_time``。此前统一读
    ``event.run_times``（不存在的属性），异常被 ``_on_event`` 吞成一行
    warning，导致 skipped 日志整条链路永不落地。
    """
    times = getattr(event, "scheduled_run_times", None)
    if times:
        return times[-1]
    return getattr(event, "scheduled_run_time", None)


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


def api_owns_next_run() -> bool:
    """API 进程是否拥有 ``next_run_at`` 写权（A-9 / 3-6 单一写者机制）。

    - ``embedded`` / ``disabled``：API 进程持有调度器或负责计算，拥有写权；
    - ``standalone``：写权在独立调度进程，API 进程**只读**，不写 ``next_run_at``，
      否则多实例下「下次执行」会抖动。
    """
    return settings.scheduler_mode != "standalone"


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
        try:
            await self.sync_all()
        except BaseException:
            # 调度器已经 firing 了：sync_all() 失败（DB 故障等）必须整体回滚，
            # 否则模块级单例上留着一个「在跑但没同步任何任务」的调度器。
            await self.shutdown()
            raise
        logger.info(
            "scheduler started mode=%s jobs=%s timezone=%s",
            settings.scheduler_mode,
            len(self.scheduler.get_jobs()),
            self.scheduler.timezone,
        )

    async def resync_timezone(self) -> None:
        """系统时区或调度时区变了：调度器实例的时区不可改，只能整体重建一次。

        采用「先建后换」：只有新实例同步成功才摘掉旧实例。若先 shutdown 再
        start，start 中途 DB 抖动会让 self.scheduler 停在 None，而后续每轮
        ``if not self.running: return`` 又直接短路——调度器永久失联且不会自愈。
        """
        if not self.running:
            return
        old = self.scheduler
        assert old is not None
        if old.timezone == zone(scheduler_timezone()):
            await self.sync_all()
            return
        old.pause()
        replacement = AsyncIOScheduler(timezone=zone(scheduler_timezone()))
        replacement.add_listener(self._on_event, SKIP_EVENT_MASK)
        replacement.start()
        self.scheduler = replacement
        try:
            await self.sync_all()
        except BaseException:
            # 恢复旧实例：时区重建失败不能让调度器整体消失
            self.scheduler = old
            replacement.shutdown(wait=False)
            old.resume()
            raise
        old.shutdown(wait=False)
        logger.info("scheduler timezone rebuilt: %s", replacement.timezone)

    async def shutdown(self) -> None:
        scheduler = self.scheduler
        if scheduler is None or not scheduler.running:
            self.scheduler = None
            return
        try:
            # 先停止派发新任务，再等在途任务收尾；shutdown() 会取消 executor 里
            # 剩余的全部 future（见 _inflight_job_tasks 注释）。
            scheduler.pause()
            pending = _inflight_job_tasks(scheduler)
            from app.modules.ops.schedule import service as schedule_service

            pending.update(task for task in _skip_tasks if not task.done())
            pending.update(task for task in schedule_service._background_tasks if not task.done())
            if pending:
                done, _ = await asyncio.wait(pending, timeout=SHUTDOWN_WAIT_SECONDS)
                if len(done) != len(pending):
                    logger.warning(
                        "关闭调度器时 %d/%d 个任务在 %.0fs 内未完成，将被取消",
                        len(pending) - len(done),
                        len(pending),
                        SHUTDOWN_WAIT_SECONDS,
                    )
        finally:
            # 无论等待被取消还是抛异常，都必须关掉调度器并从单例摘除：
            # 否则会留下一个仍在 firing 的调度器，而 engine 马上要被 dispose。
            scheduler.shutdown(wait=False)
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
        except Exception as exc:
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
        except Exception as exc:
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
        except Exception as exc:
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
        scheduled_at = _event_scheduled_at(event)
        task = loop.create_task(log_scheduler_skip(job_id, scheduled_at, reason))
        _skip_tasks.add(task)
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
