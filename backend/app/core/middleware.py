from __future__ import annotations

import json
import time
import uuid

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.authz import client_ip_from_scope
from app.core.config import settings
from app.core.errors import get_request_id, request_id_var
from app.core.logging import sanitize, truncate_json
from app.core.request_context import bind as bind_request_context
from app.core.request_context import build_context
from app.core.request_context import reset as reset_request_context
from app.db.session import async_session_factory
from app.modules.audit.models import OperationLog

# 安全响应头。CSP 禁掉 unsafe-inline 的脚本：模板里已无内联脚本，
# 首屏主题改由 static/js/theme-boot.js、运行参数改由 JSON 数据块注入。
# 样式仍允许内联（模板里有几十处一次性布局微调），内联样式的风险远低于内联脚本。
# img-src 需要 blob:：头像走 /auth/avatar 取字节后 createObjectURL 渲染
# （接口要 Bearer 鉴权，不能直接写成 <img src>），少了它所有带头像的页面都会
# 报 CSP 违规、头像位置空白。
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"

# 纯 ASGI 中间件：不再继承 BaseHTTPMiddleware。
# BaseHTTPMiddleware 会把 receive 包一层，流式请求体读完最后一个 chunk 后还要
# 再要一次消息才会结束，而服务器此时只会等客户端断开——multipart 上传必然死锁。
# TestClient 测不出来是因为它先把整个 body 读完再转发，等价于帮我们调了 body()。
# 这里 __call__ 原样透传 receive，只包 send 加响应头，从根上避开这个坑。

_JSON_BODY_LIMIT = 32768


def _header(scope: Scope, name: str) -> str:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or ():
        if key == target:
            return value.decode("latin-1")
    return ""


class RequestContextMiddleware:
    """给每个响应补上排障用的响应头，并绑定请求上下文。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, "x-request-id")
        request_id = incoming[:64] if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        context_token = bind_request_context(build_context(Request(scope, receive)))
        try:

            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers["X-Request-ID"] = request_id
                    headers["X-Content-Type-Options"] = "nosniff"
                    headers["X-Frame-Options"] = "DENY"
                    headers["Referrer-Policy"] = "same-origin"
                    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
                    headers["Permissions-Policy"] = PERMISSIONS_POLICY
                    headers["Cross-Origin-Opener-Policy"] = "same-origin"
                    headers["Cross-Origin-Resource-Policy"] = "same-origin"
                    # 仅在 HTTPS 部署时声明 HSTS；本地 http 下发这个头会让浏览器缓存后拒绝回退访问
                    if not settings.is_local_env:
                        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                await send(message)

            await self.app(scope, receive, send_wrapper)
        finally:
            reset_request_context(context_token)
            request_id_var.reset(token)


class OperationLogMiddleware:
    """把非 GET 的接口请求写进操作日志。"""

    MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.api_prefix = settings.api_prefix
        # The system module writes richer operation logs from its service layer.
        self.exclude_prefixes = (
            f"{self.api_prefix}/auth/login",
            f"{self.api_prefix}/auth/refresh",
            f"{self.api_prefix}/auth/logout",
            f"{self.api_prefix}/system/",
        )

    def _module(self, path: str) -> str | None:
        rest = path[len(self.api_prefix) :].strip("/")
        parts = [part for part in rest.split("/") if part]
        return parts[0] if parts else None

    def _should_log(self, scope: Scope) -> bool:
        return (
            scope.get("method", "").upper() in self.MUTATING_METHODS
            and scope.get("path", "").startswith(self.api_prefix)
            and not any(scope.get("path", "").startswith(prefix) for prefix in self.exclude_prefixes)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        should_log = self._should_log(scope)
        body_text: str | None = None
        if should_log:
            body_text, receive = await self._capture_body(scope, receive)

        status_box: list[int] = [500]

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_box[0] = message["status"]
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            await self._write_log(scope, 500, int((time.perf_counter() - started) * 1000), body_text)
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        await self._write_log(scope, status_box[0], duration_ms, body_text)

    async def _capture_body(self, scope: Scope, receive: Receive) -> tuple[str | None, Receive]:
        """读取并回放 JSON 请求体，供下游重复消费。

        只有 application/json 且不超过 32KB 才读：读进来的 body 必须原样还给
        路由层，否则 FastAPI 解析参数时会拿到空 body。这里把 body 缓冲成一条
        ``http.request`` 消息重放，等价于 Starlette 自己 body() 的缓存行为，
        但不会像 BaseHTTPMiddleware 那样在流式场景下死锁。
        """
        content_type = _header(scope, "content-type")
        content_length = int(_header(scope, "content-length") or 0)
        if not content_type.startswith("application/json") or content_length > _JSON_BODY_LIMIT:
            return None, receive

        chunks: list[bytes] = []
        more = True
        try:
            while more:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                if chunk:
                    chunks.append(chunk)
                more = message.get("more_body", False)
        except Exception:  # noqa: BLE001
            # 读 body 失败不能让请求挂掉，退回未缓冲的原始 receive
            return None, receive

        raw = b"".join(chunks)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:  # noqa: BLE001
            payload = None
        query = (scope.get("query_string") or b"").decode("latin-1")
        detail = truncate_json({"query": _parse_query(query), "body": payload})
        return detail, _replay_receive(raw, receive)

    async def _write_log(self, scope: Scope, status_code: int, duration_ms: int, detail: str | None) -> None:
        try:
            route = scope.get("route")
            action = getattr(route, "name", None) or scope.get("path", "")
            state = scope.get("state") or {}
            async with async_session_factory() as session:
                session.add(
                    OperationLog(
                        user_id=state.get("user_id"),
                        username=state.get("username"),
                        module=self._module(scope.get("path", "")),
                        action=str(action)[:128],
                        method=scope.get("method", "").upper(),
                        path=scope.get("path", "")[:255],
                        status_code=status_code,
                        success=status_code < 400,
                        duration_ms=duration_ms,
                        ip=client_ip_from_scope(scope),
                        user_agent=_header(scope, "user-agent")[:255],
                        detail=detail,
                        request_id=get_request_id() or None,
                    )
                )
                await session.commit()
        except Exception:
            # Auditing must never break the business response; the application log is the fallback.
            return


def _parse_query(raw: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(raw, keep_blank_values=True))


def _replay_receive(body: bytes, fallback: Receive) -> Receive:
    """生成一个先把已缓冲 body 一次性吐出、之后委托给原 receive 的 receive。"""
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await fallback()

    return receive


def sanitized_detail(payload: object) -> str:
    return truncate_json(sanitize(payload))
