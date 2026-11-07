"""P0-1 / P0-2：Cookie 认证接口同源校验 与 CORS 配置收敛。"""

from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.core import csrf
from app.core.config import Settings


def _make_request(headers: dict[str, str], url: str = "http://testserver/api/v1/auth/refresh") -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/refresh",
        "raw_path": b"/api/v1/auth/refresh",
        "headers": raw_headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("1.2.3.4", 1234),
        "root_path": "",
        "path_params": {},
        "query_params": {},
        "state": {},
    }
    return Request(scope)
    # TestClient 内部把 url 解析成 http://testserver/...，手动补 host 以匹配真实行为


class TestRequestOrigin:
    def test_origin_header_preferred(self) -> None:
        req = _make_request({"origin": "http://example.com", "referer": "http://other.com/a"})
        assert csrf.request_origin(req) == "http://example.com"

    def test_referer_fallback(self) -> None:
        req = _make_request({"referer": "http://example.com/admin/login"})
        assert csrf.request_origin(req) == "http://example.com"

    def test_none_when_no_headers(self) -> None:
        req = _make_request({})
        assert csrf.request_origin(req) is None

    def test_null_origin_rejected(self) -> None:
        req = _make_request({"origin": "null"})
        assert csrf.request_origin(req) is None


class TestIsSameOrigin:
    def test_same_origin_allowed(self) -> None:
        req = _make_request({"origin": "http://testserver"})
        assert csrf.is_same_origin(req) is True

    def test_cross_origin_rejected(self) -> None:
        req = _make_request({"origin": "http://evil.example.com"})
        assert csrf.is_same_origin(req) is False

    def test_configured_cors_origin_allowed(self) -> None:
        # conftest 配置了 APP_CORS_ORIGINS=http://localhost:8000
        req = _make_request({"origin": "http://localhost:8000"})
        assert csrf.is_same_origin(req) is True

    def test_no_origin_non_browser_allowed(self) -> None:
        req = _make_request({})
        assert csrf.is_same_origin(req) is True


class TestCorsConfigValidation:
    def test_wildcard_origin_rejected(self) -> None:
        with pytest.raises(ValueError, match="APP_CORS_ORIGINS"):
            Settings(app_cors_origins="*")

    def test_wildcard_in_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="APP_CORS_ORIGINS"):
            Settings(app_cors_origins="https://a.example.com, *")

    def test_explicit_origins_accepted(self) -> None:
        loaded = Settings(app_cors_origins="https://a.example.com,https://b.example.com")
        assert loaded.cors_origins == ["https://a.example.com", "https://b.example.com"]

    def test_empty_origins_accepted(self) -> None:
        loaded = Settings(app_cors_origins="")
        assert loaded.cors_origins == []


class TestRefreshEndpoint:
    def _login_and_get_cookie(self, client: TestClient) -> str:
        # 共享 client 的 cookie jar 会被其他测试文件的 refresh 轮换/手工改写污染，
        # 这里先清空再登录，保证拿到的是本次登录刚签发、尚未使用过的 refresh token。
        client.cookies.clear()
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Owner123456"},
        )
        assert login_resp.status_code == 200, login_resp.text
        token = client.cookies.get("fastcms_refresh")
        assert token
        return token

    def test_cross_origin_refresh_rejected(self, client, admin_token) -> None:
        # 跨源：Origin 不匹配且不在 CORS 白名单 → 403，不进入业务逻辑
        resp = client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://evil.example.com"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "AUTH_ORIGIN_DENIED"

    def test_same_origin_refresh_normal(self, client, admin_token) -> None:
        # 先登录拿到 refresh Cookie（TestClient 自动维护 cookie jar）
        self._login_and_get_cookie(client)
        # 同源刷新：不被 403 拒绝，成功轮换访问令牌
        resp = client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200, resp.text
        assert "access_token" in resp.json()["data"]

    def test_configured_cors_origin_refresh_normal(self, client, admin_token) -> None:
        self._login_and_get_cookie(client)
        # 分域部署：白名单来源允许（不校验 Cookie 是否存在，只验证不被同源拦截）
        resp = client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://localhost:8000"},
        )
        assert resp.status_code != 403, resp.text


class TestCorsPreflight:
    def test_preflight_returns_whitelisted_scope(self, client) -> None:
        resp = client.options(
            "/api/v1/auth/refresh",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, authorization",
            },
        )
        assert resp.status_code == 200, resp.text
        allow_origin = resp.headers.get("access-control-allow-origin")
        assert allow_origin == "http://localhost:8000"
        # 白名单化：不允许通配方法，也不允许未列出的方法
        allow_methods = resp.headers.get("access-control-allow-methods", "")
        assert "*" not in allow_methods
        assert "POST" in allow_methods
        assert "TRACE" not in allow_methods
        allow_headers = resp.headers.get("access-control-allow-headers", "")
        assert "*" not in allow_headers
        assert "authorization" in allow_headers.lower()
