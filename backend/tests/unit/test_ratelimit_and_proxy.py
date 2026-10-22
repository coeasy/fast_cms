from __future__ import annotations

from starlette.requests import Request

from app.core.authz import client_ip
from app.core.config import settings
from app.core.ratelimit import RATE_RULES, RateLimitMiddleware, _principal_identity
from app.core.request_context import build_context


def make_request(headers: dict[str, str], path: str = "/api/v1/auth/login", method: str = "POST") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("203.0.113.7", 5555),
        "server": ("testserver", 80),
    }
    return Request(scope)


def make_scope(headers: dict[str, str], path: str = "/api/v1/auth/login", method: str = "POST") -> dict:
    """中间件改纯 ASGI 后按 scope 取值，测试也要跟着按 scope 构造。"""
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("203.0.113.7", 5555),
        "server": ("testserver", 80),
    }


def test_forwarded_for_is_ignored_unless_proxy_is_trusted(monkeypatch) -> None:
    request = make_request({"x-forwarded-for": "198.51.100.9, 203.0.113.7"})
    monkeypatch.setattr(settings, "trust_proxy", False)
    # 默认不信任客户端可伪造的 X-Forwarded-For，否则限流和审计都会被绕过
    assert client_ip(request) == "203.0.113.7"
    monkeypatch.setattr(settings, "trust_proxy", True)
    assert client_ip(request) == "198.51.100.9"


def test_request_context_exposes_ip_and_agent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trust_proxy", False)
    context = build_context(
        make_request({"x-forwarded-for": "198.51.100.9", "user-agent": "pytest/1.0"})
    )
    assert context.ip == "203.0.113.7"
    assert context.user_agent == "pytest/1.0"


def test_rules_match_sensitive_endpoints() -> None:
    matched = {rule.name for rule in RATE_RULES if rule.matches("GET", "/api/v1/monitor/login-logs/export")}
    assert "export" in matched
    assert any(rule.matches("POST", "/api/v1/auth/change-password") for rule in RATE_RULES)
    assert not any(rule.matches("GET", "/api/v1/dashboard/summary") for rule in RATE_RULES)


def test_every_rate_rule_matches_a_live_endpoint() -> None:
    """匹配不到任何端点的规则等于没配，而且不会有任何报错提示。

    历史上 ``export`` 规则用 ``endswith("/export")``，只盖住了
    ``.../logs/export``，把整个 ``/export/{资源}`` 模块漏在外面——
    那正是最贵的读操作。新增一族端点时必须回头看一遍这里。
    """
    from app.main import app

    schema = app.openapi()
    pairs = [
        (method.upper(), path)
        for path, item in schema["paths"].items()
        for method in item
        if method.upper() in {"POST", "GET", "PUT", "PATCH", "DELETE"}
    ]
    assert pairs, "没能从 OpenAPI 里取到任何端点，测试本身失效了"
    for rule in RATE_RULES:
        hits = [pair for pair in pairs if rule.matches(*pair)]
        assert hits, f"限流规则 {rule.name} 没有命中任何端点"


def test_export_rule_covers_the_generic_export_module() -> None:
    export_rule = next(rule for rule in RATE_RULES if rule.name == "export")
    for path in ("/api/v1/export/users", "/api/v1/export/roles", "/api/v1/export/dicts"):
        assert export_rule.matches("GET", path), path
    assert export_rule.matches("GET", "/api/v1/monitor/login-logs/export")
    # 前缀不能宽到把普通查询也圈进来
    assert not export_rule.matches("GET", "/api/v1/system/users")


def test_principal_identity_requires_a_valid_token() -> None:
    from app.core.security import create_access_token

    token = create_access_token(42, "sid", 1)
    assert _principal_identity(make_scope({"authorization": f"Bearer {token}"})) == "42"
    assert _principal_identity(make_scope({"authorization": "Bearer not-a-token"})) is None
    assert _principal_identity(make_scope({})) is None


def test_middleware_is_registered_on_the_app() -> None:
    from app.main import app

    assert any(item.cls is RateLimitMiddleware for item in app.user_middleware)
