"""P1-8：任务租约（sys_job_lock）批量申请必须原子 —— 要么全部拿到，要么无部分锁残留。"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db.base import utcnow
from app.db.session import session_scope
from app.modules.ops.schedule.locks import acquire_locks
from app.modules.ops.schedule.models import JobLock

KEY_A = "lease:atomic:a"
KEY_B = "lease:atomic:b"
KEY_C = "lease:atomic:c"


async def _expire(key: str) -> None:
    async with session_scope() as db:
        await db.execute(
            update(JobLock)
            .where(JobLock.lock_key == key)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )


async def test_multi_key_acquire_is_all_or_nothing(client: TestClient) -> None:
    lease = await acquire_locks([KEY_A, KEY_B], ttl=30)
    assert lease is not None
    try:
        # 已持有 KEY_A，再申请 [KEY_A, KEY_C] 必须整批失败
        blocked = await acquire_locks([KEY_A, KEY_C], ttl=30)
        assert blocked is None, "任一 key 被占用时整批应失败"

        # 关键断言：失败不能留下部分锁 —— KEY_C 仍可被独立申请
        c_lease = await acquire_locks([KEY_C], ttl=30)
        assert c_lease is not None, "失败申请不应给空闲 key 留下部分锁"
        await c_lease.release()

        # 重复申请同一批（全部已持有）也应失败，且不产生额外行
        assert await acquire_locks([KEY_A, KEY_B], ttl=30) is None
    finally:
        await lease.release()


async def test_expired_lock_can_be_stolen_atomically(client: TestClient) -> None:
    lease = await acquire_locks([KEY_A], ttl=30)
    assert lease is not None
    try:
        await _expire(KEY_A)
        # KEY_A 已过期，连同空闲的 KEY_B 一起申请，应整批成功（抢占过期 + 新获）
        renewed = await acquire_locks([KEY_A, KEY_B], ttl=30)
        assert renewed is not None, "过期锁应被单事务抢占"
        await renewed.release()
    finally:
        await lease.release()


async def test_release_removes_all_keys_in_one_go(client: TestClient) -> None:
    lease = await acquire_locks([KEY_A, KEY_B, KEY_C], ttl=30)
    assert lease is not None
    await lease.release()
    # 释放后整批都可重新申请
    again = await acquire_locks([KEY_A, KEY_B, KEY_C], ttl=30)
    assert again is not None
    await again.release()
