from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.modules.notify import channels, service
from app.modules.notify.models import NotifyRecord
from tests.conftest import auth

API = settings.api_prefix


def _channel_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": f"通知渠道 {utcnow().timestamp()}",
        "channel_type": "dingtalk",
        "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=secrettoken123456",
        "secret": "SECabc123456",
        "events": ["job.failed", "notice.published"],
        "status": "enabled",
        "remark": "集成测试渠道",
    }
    payload.update(overrides)
    return payload


def _create_channel(client: TestClient, token: str, **overrides: Any) -> dict[str, Any]:
    response = client.post(f"{API}/notify/channels", headers=auth(token), json=_channel_payload(**overrides))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _record(dedupe_key: str) -> NotifyRecord | None:
    async with session_scope() as db:
        return (
            await db.execute(select(NotifyRecord).where(NotifyRecord.dedupe_key == dedupe_key))
        ).scalars().first()


def test_channel_crud_never_returns_raw_credentials(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    payload = _channel_payload()
    created = client.post(f"{API}/notify/channels", headers=headers, json=payload)
    assert created.status_code == 200, created.text
    channel = created.json()["data"]

    assert "secrettoken123456" not in channel["webhook_url_masked"]
    assert "webhook_url" not in channel and "secret" not in channel
    assert channel["has_secret"] is True
    assert channel["events"] == ["job.failed", "notice.published"]

    detail = client.get(f"{API}/notify/channels/{channel['id']}", headers=headers)
    assert detail.status_code == 200
    assert "secrettoken123456" not in detail.text
    assert "SECabc123456" not in detail.text

    # 不传 webhook_url 与 secret 时保留原值，显式传空串才清除密钥。
    updated = client.put(
        f"{API}/notify/channels/{channel['id']}",
        headers=headers,
        json={"secret": "", "remark": "已改"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()["data"]
    assert body["has_secret"] is False
    assert body["webhook_url_masked"] == channel["webhook_url_masked"]

    duplicate = client.post(f"{API}/notify/channels", headers=headers, json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "NOTIFY_CHANNEL_EXISTS"

    removed = client.delete(f"{API}/notify/channels/{channel['id']}", headers=headers)
    assert removed.status_code == 200
    assert client.get(f"{API}/notify/channels/{channel['id']}", headers=headers).status_code == 404


def test_channel_rejects_private_and_invalid_targets(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "notify_allow_private_target", False)
    response = client.post(
        f"{API}/notify/channels",
        headers=auth(admin_token),
        json={
            "name": "内网探测渠道",
            "channel_type": "webhook",
            "webhook_url": "http://127.0.0.1:8000/admin",
            "events": ["job.failed"],
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "NOTIFY_URL_INVALID"

    bad_event = client.post(
        f"{API}/notify/channels",
        headers=auth(admin_token),
        json={
            "name": "非法事件渠道",
            "channel_type": "webhook",
            "webhook_url": "https://example.com/hook",
            "events": ["user.deleted"],
        },
    )
    assert bad_event.status_code == 422


def test_notice_publish_queues_once_and_test_send_delivers(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = auth(admin_token)
    channel = _create_channel(client, admin_token, events=["notice.published"])

    sent: list[dict[str, Any]] = []

    async def fake_send(**kwargs: Any) -> channels.SendResult:
        sent.append(kwargs)
        return channels.SendResult(True, None, 200)

    monkeypatch.setattr(channels, "send_markdown", fake_send)

    notice = client.post(
        f"{API}/content/notices",
        headers=headers,
        json={"title": "发布推送验证", "content": "正文内容", "status": "draft"},
    )
    assert notice.status_code == 200, notice.text
    notice_id = notice.json()["data"]["id"]

    # 重复发布只推送一次：去重键是公告 id，唯一约束拦住第二次入队。
    for _ in range(2):
        published = client.post(f"{API}/content/notices/{notice_id}/publish", headers=headers)
        assert published.status_code == 200, published.text

    records = client.get(
        f"{API}/notify/records", headers=headers, params={"keyword": f"notice:{notice_id}"}
    ).json()["data"]["items"]
    assert len(records) == 1
    assert records[0]["status"] == "pending"
    assert records[0]["event"] == "notice.published"

    tested = client.post(f"{API}/notify/channels/{channel['id']}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["data"]["status"] == "success"
    assert sent[-1]["channel_type"] == "dingtalk"
    assert sent[-1]["event"] == "test"
    assert sent[-1]["dedupe_key"] == tested.json()["data"]["dedupe_key"]

    detail = client.get(f"{API}/notify/records/{records[0]['id']}", headers=headers)
    assert "正文内容" in detail.json()["data"]["content"]

    client.delete(f"{API}/notify/channels/{channel['id']}", headers=headers)
    client.delete(f"{API}/content/notices/{notice_id}", headers=headers)


async def test_dispatch_backs_off_failures_and_manual_retry_succeeds(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = auth(admin_token)
    channel = _create_channel(client, admin_token, events=["job.failed"])

    async def failing_send(**kwargs: Any) -> channels.SendResult:
        return channels.SendResult(False, "网络不可达", 503)

    monkeypatch.setattr(channels, "send_markdown", failing_send)

    # 测试消息只做一次同步投递，失败直接终态，不占用自动重试额度。
    tested = client.post(f"{API}/notify/channels/{channel['id']}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["data"]["status"] == "failed"
    assert tested.json()["data"]["response_status"] == 503

    dedupe = f"manual-{utcnow().timestamp()}"
    async with session_scope() as db:
        assert await service.enqueue(
            db, event="job.failed", dedupe_key=dedupe, title="任务失败推送", content="- 任务编码：demo"
        ) == 1

    await service.dispatch(limit=50)
    record = await _record(dedupe)
    assert record is not None
    assert record.status == "pending"
    assert record.attempts == 1
    assert record.next_retry_at > utcnow()
    retry_id = record.id

    # 退避窗口内不会被再次抢走。
    await service.dispatch(limit=50)
    after_backoff = await _record(dedupe)
    assert after_backoff is not None and after_backoff.attempts == 1

    async def ok_send(**kwargs: Any) -> channels.SendResult:
        return channels.SendResult(True, None, 200)

    monkeypatch.setattr(channels, "send_markdown", ok_send)
    retried = client.post(f"{API}/notify/records/{retry_id}/retry", headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["status"] == "success"

    again = client.post(f"{API}/notify/records/{retry_id}/retry", headers=headers)
    assert again.status_code == 409
    assert again.json()["code"] == "NOTIFY_RECORD_SENT"

    missing = client.post(f"{API}/notify/records/9999999/retry", headers=headers)
    assert missing.status_code == 404

    # 投递中的记录不允许再次手动重试，否则同一消息会重复推送
    async with session_scope() as db:
        inFlight = await db.get(NotifyRecord, retry_id)
        assert inFlight is not None
        inFlight.status = "sending"
    sending = client.post(f"{API}/notify/records/{retry_id}/retry", headers=headers)
    assert sending.status_code == 409
    assert sending.json()["code"] == "NOTIFY_RECORD_SENDING"
    async with session_scope() as db:
        inFlight = await db.get(NotifyRecord, retry_id)
        assert inFlight is not None
        inFlight.status = "success"

    client.delete(f"{API}/notify/channels/{channel['id']}", headers=headers)


async def test_disabled_channels_are_not_queued_and_records_are_skipped(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = auth(admin_token)
    channel = _create_channel(client, admin_token, events=["job.failed"])
    dedupe = f"disabled-{utcnow().timestamp()}"

    async with session_scope() as db:
        assert await service.enqueue(
            db, event="job.failed", dedupe_key=dedupe, title="启停验证", content="正文"
        ) == 1

    stopped = client.put(
        f"{API}/notify/channels/{channel['id']}", headers=headers, json={"status": "disabled"}
    )
    assert stopped.status_code == 200
    dedupe_second = f"disabled-{utcnow().timestamp()}-2"
    async with session_scope() as db:
        assert await service.enqueue(
            db, event="job.failed", dedupe_key=dedupe_second, title="启停验证", content="正文"
        ) == 0

    async def never_send(**kwargs: Any) -> channels.SendResult:
        raise AssertionError("停用渠道不应发送")

    monkeypatch.setattr(channels, "send_markdown", never_send)
    await service.dispatch(limit=50)
    record = await _record(dedupe)
    assert record.status == "skipped"
    assert "渠道" in (record.last_error or "")

    client.delete(f"{API}/notify/channels/{channel['id']}", headers=headers)
