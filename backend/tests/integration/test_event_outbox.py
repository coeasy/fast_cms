"""3-2 事务性事件发件箱的集成测试。

验证：
1. ``enqueue_event`` 在调用方事务里写 outbox，回滚后条目随之消失；
2. ``drain_event_outbox`` 派发待投递事件并写 ``EventLog``；
3. 无订阅者的事件不会被静默判为成功（尝试上限后转 failed）；
4. 管理端 ``/monitor/event-logs`` 端点可查询投递记录（A-4 ① 可追溯）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import events
from app.core.config import settings
from app.db.session import session_scope
from app.kernel.models import EventLog, EventOutbox
from tests.conftest import auth

API = settings.api_prefix


@pytest.fixture(autouse=True)
def _isolated_events():
    """每个用例用干净订阅表，结束后恢复真实订阅（避免污染其它测试文件）。

    真实模块在应用启动时已注册订阅；用例内 reset 后注册测试订阅者，
    结束后把真实订阅者原样放回，保证跨文件执行顺序无关。
    """
    snapshot = {event: list(events.subscribers(event)) for event in events.KNOWN_EVENTS}
    events.reset()
    yield
    events.reset()
    for event, handlers in snapshot.items():
        for handler in handlers:
            events._handlers[event].append(handler)


async def test_enqueue_rolls_back_with_transaction(client: TestClient) -> None:
    """enqueue_event 落 outbox 行；事务回滚后条目不残留（同事务原子性）。"""
    async with session_scope() as db:
        entry_id = await events.enqueue_event(db, "notice.published", notice_id=7)
        assert entry_id > 0
        await db.rollback()
    async with session_scope() as db:
        row = (
            await db.execute(select(EventOutbox).where(EventOutbox.id == entry_id))
        ).scalars().first()
        assert row is None, "事务回滚后 outbox 条目不应残留"


async def test_drain_dispatches_and_writes_event_log(client: TestClient) -> None:
    """drain_event_outbox 派发待投递事件，落 done 并写 EventLog。"""
    calls: list[dict[str, Any]] = []

    async def handler(**payload: Any) -> None:
        calls.append(dict(payload))

    events.subscribe("notice.published", handler)
    async with session_scope() as db:
        await events.enqueue_event(db, "notice.published", notice_id=42, title="hello")
        await db.commit()

    async with session_scope() as db:
        count = await events.drain_event_outbox(db, limit=10)
        await db.commit()
        assert count == 1

    assert calls == [{"notice_id": 42, "title": "hello"}]
    async with session_scope() as db:
        rows = (await db.execute(select(EventOutbox))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "done"
        assert rows[0].attempts == 1
        logs = (await db.execute(select(EventLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].event == "notice.published"
        assert logs[0].success is True


async def test_drain_without_subscriber_marks_failed_after_retry_cap(client: TestClient) -> None:
    """没有任何订阅者成功处理：达到重试上限后转 failed，不无限轮询。"""
    events.reset()  # 确保没有真实订阅者
    async with session_scope() as db:
        await events.enqueue_event(db, "notice.published", notice_id=1)
        await db.commit()

    async with session_scope() as db:
        for _ in range(5):
            await events.drain_event_outbox(db, limit=10)
            await db.commit()

    async with session_scope() as db:
        rows = (await db.execute(select(EventOutbox))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].attempts == 5


def test_event_logs_endpoint_returns_delivery_records(client: TestClient, admin_token: str) -> None:
    """管理端可查事件投递日志（A-4 ①）。"""
    headers = auth(admin_token)
    response = client.get(f"{API}/monitor/event-logs", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert "items" in body and "total" in body
