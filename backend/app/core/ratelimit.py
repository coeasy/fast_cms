from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.authz import client_ip_from_scope
from app.core.cache import get_cache
from app.core.config import settings
from app.core.errors import error_payload
from app.core.security import decode_access_token


@dataclass(frozen=True)
class RateRule:
    name: str
    methods: tuple[str, ...]
    suffixes: tuple[str, ...]
    limit: int
    window: int
    by_principal: bool
    # 前缀匹配：整组同族端点（如 /export/{资源}）共用一个限额时用，
    # 否则只能把每个资源名都枚举一遍，新增一个导出类型就会漏配。
    prefixes: tuple[str, ...] = ()

    def matches(self, method: str, path: str) -> bool:
        if method.upper() not in self.methods:
            return False
        if self.prefixes and path.startswith(self.prefixes):
            return True
        return any(path.endswith(suffix) for suffix in self.suffixes)


# 单位窗口内的请求上限；登录限流在 /auth/login 里单独实现（需要写登录日志并区分成功/失败）
RATE_RULES: tuple[RateRule, ...] = (
    RateRule("refresh", ("POST",), ("/auth/refresh",), 120, 60, False),
    RateRule("change-password", ("POST",), ("/auth/change-password",), 5, 60, True),
    RateRule("profile-update", ("PUT",), ("/auth/profile",), 20, 60, True),
    RateRule("avatar-upload", ("POST",), ("/auth/avatar",), 10, 300, True),
    RateRule("file-upload", ("POST",), ("/tool/files/upload",), 30, 60, True),
    # 导出是最贵的读操作（整表落地），/export/{资源} 这一族必须和 .../logs/export 一样限流
    RateRule(
        "export",
        ("GET",),
        ("/export",),
        10,
        60,
        True,
        prefixes=(f"{settings.api_prefix}/export/",),
    ),
    # CSV 导入会跑完整的建用户写路径，按上传同等强度限流
    RateRule("user-import", ("POST",), ("/system/users/import",), 10, 60, True),
    RateRule("reset-password", ("POST",), ("/reset-password",), 10, 60, True),
    RateRule("log-cleanup", ("DELETE",), ("-logs",), 10, 60, True),
    RateRule("codegen", ("POST",), ("/codegen/preview", "/codegen/download"), 20, 60, True),
    RateRule("codegen-write", ("POST",), ("/codegen/write",), 5, 300, True),
)


def _scope_header(scope: Scope, name: str) -> str:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or ():
        if key == target:
            return value.decode("latin-1")
    return ""


def _principal_identity(scope: Scope) -> str | None:
    # 限流键需要身份，但中间件跑在鉴权依赖之前；访问令牌是本地验签的，可以直接取 sub。
    token = _scope_header(scope, "authorization")
    if not token.lower().startswith("bearer "):
        return None
    try:
        payload = decode_access_token(token[7:].strip())
    except Exception:  # noqa: BLE001
        return None
    return str(payload.get("sub") or "") or None


async def _consume(rule: RateRule, identity: str) -> bool:
    attempts = await get_cache().incr(f"fastcms:rate:{rule.name}:{identity}", rule.window)
    return attempts > rule.limit


@lru_cache(maxsize=1)
def _api_prefix() -> str:
    return settings.api_prefix


class RateLimitMiddleware:
    """对刷新令牌、改密、上传、导出等敏感接口做限流。

    纯 ASGI 实现：``BaseHTTPMiddleware`` 会让 multipart 上传死锁（详见
    ``app/core/middleware.py`` 顶部的说明），所以这里直接收发 ASGI 消息。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        prefix = _api_prefix()
        if not path.startswith(prefix):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        for rule in RATE_RULES:
            if not rule.matches(method, path):
                continue
            principal = _principal_identity(scope) if rule.by_principal else None
            identity = principal or client_ip_from_scope(scope) or "unknown"
            if await _consume(rule, identity):
                await self._reject(send, rule)
                return
            break

        await self.app(scope, receive, send)

    async def _reject(self, send: Send, rule: RateRule) -> None:
        # 中间件在 FastAPI 异常处理层之外，必须自己拼响应而不是抛 AppError
        payload = json.dumps(
            error_payload(
                "AUTH_RATE_LIMITED",
                f"请求过于频繁，请 {rule.window} 秒后再试",
                {"limit": rule.limit, "window": rule.window},
            ),
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(payload)).encode("latin-1")),
                    (b"retry-after", str(rule.window).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
