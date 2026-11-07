"""P0-7：登录失败计数、IP 自动封禁与异常分类可追踪。"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError
from app.db.session import session_scope
from app.kernel.models import IpRule
from app.modules.sys.security import service as security_service
from tests.conftest import auth

API = settings.api_prefix
# TEST-NET-3 保留地址，用作测试来源 IP，避免污染真实封禁数据
TEST_IP = "203.0.113.50"
TEST_IP_B = "203.0.113.51"


def _fail(client: TestClient, ip: str, username: str = "ghost_user") -> int:
    resp = client.post(
        f"{API}/auth/login",
        json={"username": username, "password": "WrongPass12345"},
        headers={"X-Forwarded-For": ip},
    )
    return resp.status_code


def _success(client: TestClient, ip: str, username: str, password: str) -> int:
    resp = client.post(
        f"{API}/auth/login",
        json={"username": username, "password": password},
        headers={"X-Forwarded-For": ip},
    )
    return resp.status_code


async def _ban_rule(ip: str) -> IpRule | None:
    async with session_scope() as db:
        return (
            await db.execute(select(IpRule).where(IpRule.target.like(f"{ip}%")))
        ).scalars().first()


async def _delete_rule(ip: str) -> None:
    async with session_scope() as db:
        for rule in (await db.execute(select(IpRule).where(IpRule.target.like(f"{ip}%")))).scalars().all():
            await db.delete(rule)


async def _cache_counter(ip: str) -> int | None:
    from app.core.cache import get_cache

    return await get_cache().get(f"fastcms:login-fail:{ip}")


async def test_ip_auto_ban_after_repeated_failures(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "trust_proxy", True)
    monkeypatch.setattr(settings, "auth_max_failed", 3)
    monkeypatch.setattr(settings, "auth_ip_ban_minutes", 30)

    try:
        # 前 2 次失败：未达阈值，不产生封禁
        assert _fail(client, TEST_IP) == 401
        assert _fail(client, TEST_IP) == 401
        # 第 3 次失败：触发自动封禁
        assert _fail(client, TEST_IP) == 401

        rule = await _ban_rule(TEST_IP)
        assert rule is not None, "连续失败应触发 IP 自动封禁"
        assert rule.kind == "block"
        assert rule.auto is True
        assert "自动封禁" in (rule.reason or "")

        # 封禁成功后计数被重置，避免下一窗口反复尝试重复封禁
        assert await _cache_counter(TEST_IP) is None

        # 已封禁来源再请求会被 IP 守卫拦下
        blocked = client.post(
            f"{API}/auth/login",
            json={"username": "ghost_user", "password": "WrongPass12345"},
            headers={"X-Forwarded-For": TEST_IP},
        )
        assert blocked.status_code == 403
    finally:
        await _delete_rule(TEST_IP)


async def test_successful_login_resets_ip_failure_counter(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "trust_proxy", True)
    monkeypatch.setattr(settings, "auth_max_failed", 5)
    headers = auth(admin_token)
    created = client.post(
        f"{API}/system/users",
        headers=headers,
        json={
            "username": "reset_counter_user",
            "nickname": "计数重置",
            "password": "ResetCounter123",
            "must_change_password": False,
        },
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["data"]["id"]
    try:
        assert _fail(client, TEST_IP_B, "reset_counter_user") == 401
        assert _fail(client, TEST_IP_B, "reset_counter_user") == 401
        assert await _cache_counter(TEST_IP_B) == 2

        # 成功登录：IP 失败计数清零
        assert _success(client, TEST_IP_B, "reset_counter_user", "ResetCounter123") == 200
        assert await _cache_counter(TEST_IP_B) is None
    finally:
        assert client.delete(f"{API}/system/users/{user_id}", headers=headers).status_code == 200


async def test_ban_failure_is_logged_not_swallowed(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "trust_proxy", True)
    monkeypatch.setattr(settings, "auth_max_failed", 2)

    async def failing_ban(db: Any, ctx: Any, **kwargs: Any) -> dict[str, Any]:
        raise AppError("SECURITY_IP_INVALID", "模拟封禁失败", 422)

    monkeypatch.setattr(security_service, "block_ip_now", failing_ban)

    with caplog.at_level(logging.ERROR, logger="fastcms.auth"):
        assert _fail(client, TEST_IP_B) == 401
        assert _fail(client, TEST_IP_B) == 401

    # 真实封禁失败必须告警且携带 request_id，不能被静默吞掉
    assert any("自动封禁失败" in rec.message for rec in caplog.records), caplog.text
    assert any(
        "自动封禁失败" in rec.message and "request_id=" in rec.message
        for rec in caplog.records
    )
