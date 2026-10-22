from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.events import EVENT_JOB_MAX_INSTANCES
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.tz import scheduler_timezone
from app.db.base import utcnow
from app.modules.schedule.models import Job
from app.modules.schedule.scheduler import (
    SKIP_EVENT_MASK,
    SchedulerService,
    build_trigger,
)

TZ = ZoneInfo("Asia/Shanghai")
INTERVAL = 300


def make_job(**overrides) -> Job:
    job = Job(
        id=987,
        name="锚点测试",
        code="demo.anchor",
        job_type="registry",
        trigger_type="interval",
        interval_seconds=INTERVAL,
        timezone="Asia/Shanghai",
        status="enabled",
        timeout_seconds=30,
        args_json={},
    )
    for field, value in overrides.items():
        setattr(job, field, value)
    return job


def test_interval_without_anchor_starts_from_now():
    now = utcnow().astimezone(TZ)
    fired = build_trigger(make_job()).get_next_fire_time(None, now)
    assert fired is not None
    assert timedelta(seconds=299) < fired - now < timedelta(seconds=301)


def test_interval_anchor_is_kept_verbatim():
    now = utcnow().astimezone(TZ)
    anchor = now + timedelta(seconds=137)
    fired = build_trigger(make_job(), anchor=anchor).get_next_fire_time(None, now)
    assert fired == anchor


def test_stale_anchor_resumes_on_the_original_phase():
    now = utcnow().astimezone(TZ)
    anchor = now - timedelta(seconds=970)
    fired = build_trigger(make_job(), anchor=anchor).get_next_fire_time(None, now)
    assert fired is not None
    assert fired > now
    assert (fired - anchor).total_seconds() % INTERVAL == 0


def test_anchor_is_ignored_by_cron_and_date_triggers():
    now = utcnow().astimezone(TZ)
    anchor = now + timedelta(seconds=37)
    cron = build_trigger(make_job(trigger_type="cron", cron_expr="0 3 * * *"), anchor=anchor)
    assert cron.get_next_fire_time(None, now).minute == 0
    run_at = now + timedelta(hours=1)
    date_trigger = build_trigger(make_job(trigger_type="date", run_at=run_at), anchor=anchor)
    assert date_trigger.get_next_fire_time(None, now) == run_at.astimezone(TZ)


@pytest.fixture()
async def scheduler_service():
    service = SchedulerService()
    service.scheduler = AsyncIOScheduler(timezone=ZoneInfo(scheduler_timezone()))
    service.scheduler.start()
    try:
        yield service
    finally:
        service.scheduler.shutdown(wait=False)
        service.scheduler = None


async def test_reload_preserves_the_persisted_next_run(scheduler_service: SchedulerService):
    job = make_job()
    anchor = utcnow() + timedelta(seconds=137)
    job.next_run_at = anchor

    first = scheduler_service._add(job, anchor=anchor)
    assert first is not None
    assert abs((first - anchor).total_seconds()) < 1

    # a restart or a signature change must not shift the schedule by a full interval
    second = scheduler_service._add(job, anchor=first)
    assert second is not None
    assert abs((second - anchor).total_seconds()) < 1

    scheduled = scheduler_service.scheduler.get_job(str(job.id))
    assert scheduled.misfire_grace_time == settings.scheduler_misfire_grace_seconds
    assert scheduled.coalesce is True
    assert scheduled.max_instances == 1


async def test_overrun_keeps_ticking_and_reports_dropped_ticks():
    starts: list[datetime] = []
    drops: list[int] = []

    async def slow_job() -> None:
        starts.append(utcnow())
        await asyncio.sleep(0.5)

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_listener(lambda event: drops.append(event.code), EVENT_JOB_MAX_INSTANCES)
    scheduler.start()
    try:
        scheduler.add_job(
            slow_job,
            trigger=IntervalTrigger(seconds=0.15, start_date=utcnow()),
            id="slow",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=None,
        )
        await asyncio.sleep(1.2)
        scheduled = scheduler.get_job("slow")
        assert scheduled is not None
        # 读取的这一刻下一个 tick 可能刚好到期，只要求调度指针继续向前而没有卡死。
        assert scheduled.next_run_time is not None
        assert scheduled.next_run_time >= starts[-1]
    finally:
        scheduler.shutdown(wait=False)

    # the scheduler fires again while an instance is still busy: the next run time
    # comes from the schedule, not from the completion time, and the ticks that
    # cannot start are reported instead of vanishing
    assert len(starts) >= 2
    assert len(drops) >= 2


async def test_listener_mask_starts_without_error():
    service = SchedulerService()
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_listener(service._on_event, SKIP_EVENT_MASK)
    scheduler.start()
    try:
        assert scheduler.running
    finally:
        scheduler.shutdown(wait=False)


def test_repeated_skips_are_logged_once_per_window():
    service = SchedulerService()
    assert service._should_log_skip(1, "上一轮任务尚未结束，本次跳过") is True
    assert service._should_log_skip(1, "上一轮任务尚未结束，本次跳过") is False
    assert service._should_log_skip(1, "错过执行时间（调度进程繁忙）") is True
    assert service._should_log_skip(2, "错过执行时间（调度进程繁忙）") is True

    service._recent_skips[(1, "上一轮任务尚未结束，本次跳过")] = utcnow() - timedelta(seconds=61)
    assert service._should_log_skip(1, "上一轮任务尚未结束，本次跳过") is True
