"""3-5（B-13）：租约竞争型 skipped 日志的窗口聚合并发断言。

同一任务在 60 秒窗口内被调度器反复跳过（错过执行时间 / 上一轮未结束），
每次触发都不该落一条新的 skipped 行——否则一天上千条噪音把任务日志刷满。

断言：并发触发 8 次，``sys_job_log`` 里该任务的 ``skipped`` 新增恒 ≤ 1 条。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.base import utcnow
from app.db.session import session_scope
from app.jobs.runner import log_scheduler_skip
from app.modules.ops.schedule.models import Job, JobLog
from app.modules.ops.schedule.scheduler import SchedulerService

REASON_OVERRUN = "上一轮任务尚未结束，本次跳过"
REASON_MISSED = "错过执行时间（调度进程繁忙）"


async def _make_job() -> int:
    async with session_scope() as db:
        job = Job(
            name=f"skip-window-{utcnow().timestamp()}",
            code="demo.skip_window",
            job_type="registry",
            trigger_type="interval",
            interval_seconds=60,
            timezone="Asia/Shanghai",
            status="enabled",
            timeout_seconds=30,
            args_json={},
        )
        db.add(job)
        await db.flush()
        return int(job.id)


async def _skipped_count(job_id: int) -> int:
    async with session_scope() as db:
        return int(
            (
                await db.execute(
                    select(func.count()).select_from(JobLog).where(
                        JobLog.job_id == job_id, JobLog.status == "skipped"
                    )
                )
            ).scalar()
            or 0
        )


async def test_concurrent_skips_write_at_most_one_row(client: TestClient) -> None:
    """同任务、同一跳过原因并发触发 8 次，skipped 新增恒 ≤ 1 条。

    只测租约竞争型（上一轮未结束）：这是 B-13 的噪音源头——任务执行时间
    超过周期时，APScheduler 每跳一次 tick 就抛一个 MAX_INSTANCES 事件，
    过去会每个 tick 落一条 skipped。
    """
    job_id = await _make_job()
    service = SchedulerService()

    async def trigger() -> None:
        # 与 _record_skip 同口径：先过窗口聚合，放行才落库
        if service._should_log_skip(job_id, REASON_OVERRUN):
            await log_scheduler_skip(job_id, None, REASON_OVERRUN)

    # 8 个并发触发同一原因，全部完成
    await asyncio.gather(*(trigger() for _ in range(8)))

    count = await _skipped_count(job_id)
    assert count <= 1, f"并发跳过应被窗口聚合，实际写入 {count} 条"


async def test_skip_window_resets_after_elapsed(client: TestClient) -> None:
    """窗口过后再次跳过可重新落库（不永久静默）。"""
    job_id = await _make_job()
    service = SchedulerService()

    assert service._should_log_skip(job_id, REASON_OVERRUN) is True
    assert service._should_log_skip(job_id, REASON_OVERRUN) is False
    # 把窗口回拨 61 秒，模拟「上一批发生在窗口外」
    service._recent_skips[(job_id, REASON_OVERRUN)] = utcnow() - timedelta(seconds=61)
    assert service._should_log_skip(job_id, REASON_OVERRUN) is True
