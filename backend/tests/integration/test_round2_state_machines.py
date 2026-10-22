"""第 2 轮审计回归：任务状态机、通知清理与登录失败计数。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.jobs.runner import run_job
from app.modules.notify import service as notify_service
from app.modules.notify.models import NotifyRecord
from app.modules.schedule.models import Job
from app.modules.system.models import User
from tests.conftest import auth

API = "/api/v1"


async def _job(job_id: int) -> Job | None:
    async with session_scope() as db:
        return await db.get(Job, job_id)


def _create_date_job(client: TestClient, headers: dict[str, str], name: str) -> dict[str, Any]:
    response = client.post(
        f"{API}/schedule/jobs",
        headers=headers,
        json={
            "name": name,
            "code": "system.heartbeat",
            "trigger_type": "date",
            "run_at": (utcnow() + timedelta(days=1)).isoformat(),
            "timezone": "Asia/Shanghai",
            "args_json": {"message": name},
            "status": "enabled",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_manual_run_keeps_unexpired_one_shot_job_enabled(
    client: TestClient, admin_token: str
) -> None:
    headers = auth(admin_token)
    job = _create_date_job(client, headers, "一次性任务手动执行")
    assert job["status"] == "enabled"
    assert job["next_run_at"]

    await run_job(int(job["id"]), "manual")
    after = await _job(int(job["id"]))
    assert after is not None
    # 手动「立即执行」只是试跑，不能把还没到计划时间的一次性任务停用掉
    assert after.status == "enabled"
    assert after.next_run_at is not None

    assert client.delete(f"{API}/schedule/jobs/{job['id']}", headers=headers).status_code == 200


async def test_scheduler_run_pauses_finished_one_shot_job(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    job = _create_date_job(client, headers, "一次性任务调度执行")

    await run_job(int(job["id"]), "scheduler")
    after = await _job(int(job["id"]))
    assert after is not None
    assert after.status == "paused"
    assert after.next_run_at is None

    assert client.delete(f"{API}/schedule/jobs/{job['id']}", headers=headers).status_code == 200


async def test_cleanup_records_keeps_undelivered_messages(
    client: TestClient, admin_token: str
) -> None:
    headers = auth(admin_token)
    channel = client.post(
        f"{API}/notify/channels",
        headers=headers,
        json={
            "name": f"清理验证渠道 {utcnow().timestamp()}",
            "channel_type": "webhook",
            "webhook_url": "https://example.com/hook",
            "events": ["job.failed"],
        },
    )
    assert channel.status_code == 200, channel.text
    channel_id = channel.json()["data"]["id"]

    stamp = utcnow().timestamp()
    done_key = f"cleanup-{stamp}-done"
    pending_key = f"cleanup-{stamp}-pending"
    stale = utcnow() - timedelta(days=90)
    async with session_scope() as db:
        assert await notify_service.enqueue(
            db, event="job.failed", dedupe_key=done_key, title="历史记录", content="正文"
        ) == 1
        assert await notify_service.enqueue(
            db, event="job.failed", dedupe_key=pending_key, title="待投递记录", content="正文"
        ) == 1
        records = (
            await db.execute(select(NotifyRecord).where(NotifyRecord.dedupe_key.in_([done_key, pending_key])))
        ).scalars().all()
        assert len(records) == 2
        for record in records:
            record.created_at = stale
        finished = await db.execute(
            update(NotifyRecord).where(NotifyRecord.dedupe_key == done_key).values(status="success")
        )
        assert finished.rowcount == 1

    async with session_scope() as db:
        assert await notify_service.cleanup_records(db, days=30) == 1

    async with session_scope() as db:
        remaining = (
            await db.execute(select(NotifyRecord.dedupe_key).where(NotifyRecord.dedupe_key.in_([done_key, pending_key])))
        ).scalars().all()
    # 未到终态的旧记录仍在投递队列里，清理时删掉等于静默丢消息
    assert remaining == [pending_key]

    assert client.delete(f"{API}/notify/channels/{channel_id}", headers=headers).status_code == 200


async def test_failed_login_count_increments_on_every_attempt(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = client.post(
        f"{API}/system/users",
        headers=headers,
        json={
            "username": "counter_target",
            "nickname": "计数验证",
            "password": "CounterTarget123",
            "must_change_password": False,
        },
    )
    assert created.status_code == 200, created.text
    try:
        attempts = max(2, settings.auth_max_failed - 2)
        for _ in range(attempts):
            response = client.post(
                "/api/v1/auth/login", json={"username": "counter_target", "password": "WrongPass12345"}
            )
            assert response.status_code == 401

        # 每一次失败都要落库：ORM 读改写会少计，让锁定阈值被绕过
        async def read_count() -> int:
            async with session_scope() as db:
                user = (await db.execute(select(User).where(User.username == "counter_target"))).scalars().first()
                assert user is not None
                return int(user.failed_login_count)

        assert await read_count() == attempts
    finally:
        assert client.delete(f"{API}/system/users/{created.json()['data']['id']}", headers=headers).status_code == 200
