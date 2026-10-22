from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.main import create_app
from tests.conftest import auth

CUSTOM_PREFIX = "/integration/api"


def test_api_prefix_normalization() -> None:
    assert Settings(api_prefix="integration/api/").api_prefix == CUSTOM_PREFIX
    assert Settings(api_prefix="/api/v1").api_prefix == "/api/v1"
    assert Settings(api_prefix="  ").api_prefix == "/api/v1"
    # Git Bash 会把 /demo/api 转换成 Windows 路径，这类值必须在启动时报错而不是静默 404
    with pytest.raises(ValidationError, match="API_PREFIX"):
        Settings(api_prefix="/C:/Program Files/Git/demo/api")


def test_routes_and_audits_follow_the_prefix(client: TestClient, admin_token: str, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_prefix", CUSTOM_PREFIX)
    headers = auth(admin_token)
    scoped = TestClient(create_app())
    try:
        assert scoped.get("/healthz").json()["status"] == "ok"
        assert scoped.get(f"{CUSTOM_PREFIX}/auth/me", headers=headers).json()["code"] == "OK"

        # 带配置前缀的未知路径返回 JSON 404；旧前缀路径已不属于任何站点，同样 404
        missing = scoped.get(f"{CUSTOM_PREFIX}/schedule/logs")
        assert missing.status_code == 404
        assert missing.json()["code"] == "HTTP_404"
        legacy = scoped.get("/api/v1/auth/me", headers=headers)
        assert legacy.status_code == 404

        created = scoped.post(
            f"{CUSTOM_PREFIX}/system/users",
            headers=headers,
            json={
                "username": "prefix_user",
                "password": "Prefix12345",
                "nickname": "前缀用户",
                "must_change_password": False,
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["data"]["id"]

        notice = scoped.post(
            f"{CUSTOM_PREFIX}/content/notices",
            headers=headers,
            json={"title": "前缀公告", "content": "用于校验审计归属", "status": "draft"},
        )
        assert notice.status_code == 200, notice.text
        notice_id = notice.json()["data"]["id"]

        assert scoped.delete(f"{CUSTOM_PREFIX}/content/notices/{notice_id}", headers=headers).status_code == 200
        assert scoped.delete(f"{CUSTOM_PREFIX}/system/users/{user_id}", headers=headers).status_code == 200
    finally:
        scoped.close()

    logs = client.get(
        "/api/v1/monitor/operation-logs",
        headers=headers,
        params={"keyword": CUSTOM_PREFIX, "module": "content"},
    ).json()["data"]["items"]
    assert logs, "中间件应按配置前缀记录非 system 模块的操作日志"
    assert all(item["path"].startswith(CUSTOM_PREFIX) for item in logs)

    system_logs = client.get(
        "/api/v1/monitor/operation-logs",
        headers=headers,
        params={"keyword": "user.create", "module": "system", "page_size": 100},
    ).json()["data"]["items"]
    assert any(item["path"] == f"{CUSTOM_PREFIX}/system/users" for item in system_logs), (
        "service 层日志应使用配置前缀"
    )


def test_refresh_cookie_path_follows_prefix(admin_token: str, monkeypatch) -> None:
    # 依赖 admin_token 完成首登改密，使管理员密码可预期。
    assert admin_token
    monkeypatch.setattr(settings, "api_prefix", CUSTOM_PREFIX)
    scoped = TestClient(create_app())
    try:
        response = scoped.post(
            f"{CUSTOM_PREFIX}/auth/login",
            json={"username": "admin", "password": "Owner123456"},
        )
        assert response.status_code == 200, response.text
        set_cookie = response.headers["set-cookie"]
        assert f"Path={CUSTOM_PREFIX}/auth" in set_cookie, set_cookie
    finally:
        scoped.close()
