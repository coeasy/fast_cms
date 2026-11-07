from __future__ import annotations

import json
import logging
import time
import uuid
from typing import ClassVar

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.errors import get_request_id, request_id_var
from app.core.logging import truncate_json
from app.core.module import module_audit_modes
from app.core.net import client_ip_from_scope
from app.core.request_context import bind as bind_request_context
from app.core.request_context import build_context
from app.core.request_context import reset as reset_request_context
from app.db.session import async_session_factory
from app.kernel.models import OperationLog

logger = logging.getLogger("fastcms.middleware")

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
# 没有 Content-Length 的分块请求可以无限发 more_body=True；只校验单个 chunk
# 不够，必须在字节数与消息轮数上都设上限，否则内存与循环都是无界的。
_JSON_BODY_MAX_CHUNKS = 1024


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
        context_token = bind_request_context(
            build_context(Request(scope, receive), started_at=time.perf_counter())
        )
        started = time.perf_counter()
        status_box: list[int] = [500]
        try:

            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start":
                    status_box[0] = message["status"]
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
            duration_ms = int((time.perf_counter() - started) * 1000)
            # B-8 / 4-2：所有 HTTP 请求都进指标与慢请求视角，不影响业务响应
            try:
                from app.core import metrics

                metrics.record_request(
                    scope.get("method", "GET"), status_box[0], duration_ms
                )
                if duration_ms > settings.slow_request_threshold_ms:
                    logger.warning(
                        "慢请求 request_id=%s method=%s path=%s status=%s duration_ms=%s",
                        request_id,
                        scope.get("method", ""),
                        scope.get("path", ""),
                        status_box[0],
                        duration_ms,
                    )
            except Exception:
                logger.debug("记录指标失败 request_id=%s", request_id, exc_info=True)
            reset_request_context(context_token)
            request_id_var.reset(token)


class OperationLogMiddleware:
    """把非 GET 的接口请求写进操作日志。"""

    MUTATING_METHODS: ClassVar[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.api_prefix = settings.api_prefix
        # auth 的登录/刷新/登出写入 LoginLog（独立表），不走通用操作日志，
        # 且不属于任何「service 自写」模块，因此按路径排除。
        self.exclude_prefixes = (
            f"{self.api_prefix}/auth/login",
            f"{self.api_prefix}/auth/refresh",
            f"{self.api_prefix}/auth/logout",
        )

    def _module(self, path: str) -> str | None:
        rest = path[len(self.api_prefix) :].strip("/")
        parts = [part for part in rest.split("/") if part]
        return parts[0] if parts else None

    def _should_log(self, scope: Scope) -> bool:
        if scope.get("method", "").upper() not in self.MUTATING_METHODS:
            return False
        path = scope.get("path", "")
        if not path.startswith(self.api_prefix):
            return False
        if any(path.startswith(prefix) for prefix in self.exclude_prefixes):
            return False
        # 谁写谁负责：声明了 audit="service" 的模块由 service 层自写操作日志，
        # 中间件不再兜底，避免同一次请求双写（B-1）。
        module = self._module(path)
        return not (module and module_audit_modes().get(module) == "service")

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
            if should_log:
                await self._write_log(
                    scope, 500, int((time.perf_counter() - started) * 1000), body_text
                )
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        # 只在「该记」时写：should_log 决定的是「要不要记」，不只决定要不要读 body。
        # 此前 `_write_log` 无条件调用，导致 GET 请求与 audit="service" 模块的变更
        # 请求也被中间件兜底写一条 —— 正是 B-1 双写（与 service 层各写一条）的根源。
        if should_log:
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
        total = 0
        rounds = 0
        more = True
        overflow = False
        try:
            while more:
                message = await receive()
                if message["type"] != "http.request":
                    # disconnect 等其它消息不能原地打转：ASGI 规范不保证
                    # receive() 在断开后会抛异常，继续等就是死循环。
                    break
                chunk = message.get("body", b"")
                more = message.get("more_body", False)
                chunks.append(chunk)
                total += len(chunk)
                rounds += 1
                if total > _JSON_BODY_LIMIT or rounds > _JSON_BODY_MAX_CHUNKS:
                    # 超限就停止缓冲：已消费的字节用「前缀重放 + 原始 receive
                    # 续读」原样交还下游，既不丢字节也不让内存无限增长。
                    overflow = True
                    break
        except Exception:
            # 读 body 失败不能让请求挂掉；已经从 receive 消费掉的消息必须回放，
            # 否则下游会拿到空 body（此前直接退回原始 receive 是错的）。
            return None, _replay_receive(b"".join(chunks), receive, more=False)

        raw = b"".join(chunks)
        if overflow:
            # 不记 body（审计只留 query），但请求体本身完整交给路由层
            return None, _replay_receive(raw, receive, more=more)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            payload = None
        query = (scope.get("query_string") or b"").decode("latin-1")
        detail = truncate_json({"query": _parse_query(query), "body": payload})
        return detail, _replay_receive(raw, receive)

    async def _write_log(self, scope: Scope, status_code: int, duration_ms: int, detail: str | None) -> None:
        row: dict[str, object] = {
            "user_id": (scope.get("state") or {}).get("user_id"),
            "username": (scope.get("state") or {}).get("username"),
            "module": self._module(scope.get("path", "")),
            "action": str(getattr(scope.get("route"), "name", None) or scope.get("path", ""))[:128],
            "method": scope.get("method", "").upper(),
            "path": scope.get("path", "")[:255],
            "status_code": status_code,
            "success": status_code < 400,
            "duration_ms": duration_ms,
            "ip": client_ip_from_scope(scope),
            "user_agent": _header(scope, "user-agent")[:255],
            "detail": detail,
            "request_id": get_request_id() or None,
        }
        # B-7 / Phase 11-3：默认走批量队列，不再为每条审计开一个独立事务；
        # 队列未启动或已满时降级直写（审计宁可拖慢请求也不能丢）。
        from app.kernel.audit import audit_writer

        if audit_writer.enqueue(row):
            return
        await self._write_row_direct(row)

    async def _write_row_direct(self, row: dict[str, object]) -> None:
        try:
            async with async_session_factory() as session:
                session.add(OperationLog(**row))
                await session.commit()
        except Exception:
            # 审计失败不能影响业务响应，但必须留下痕迹：队列满/未启动时走的就是
            # 这条兜底路径，静默 return 等于「谁删了这条数据」永久查不到。
            logger.error(
                "操作日志直写失败（审计已丢失）path=%s status=%s request_id=%s",
                row.get("path"),
                row.get("status_code"),
                row.get("request_id"),
                exc_info=True,
            )


def _parse_query(raw: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(raw, keep_blank_values=True))


def _replay_receive(body: bytes, fallback: Receive, *, more: bool = False) -> Receive:
    """先把已消费的 body 前缀吐出，再委托给原 receive。

    ``more=True`` 用于「缓冲超限」场景：中间件提前停止读取后，剩余的分块消息
    仍由原始 ``receive`` 继续提供，下游看到的字节流与未经中间件时完全一致。
    ``more=False`` 用于「整个 body 已读完」或「读取异常」场景。
    """
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": more}
        return await fallback()

    return receive
