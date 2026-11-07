from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Request

from app.core.net import client_ip


@dataclass(frozen=True)
class HttpRequestContext:
    ip: str | None = None
    user_agent: str | None = None
    # 请求起点（time.perf_counter()）。0.0 表示无请求上下文（如后台任务），
    # 此时操作日志的 duration_ms 无法推导真实耗时（B-1 ②：不再硬编码 0，而是有据可算）。
    started_at: float = 0.0


EMPTY = HttpRequestContext()

_request_context: ContextVar[HttpRequestContext] = ContextVar("http_request_context", default=EMPTY)


def build_context(request: Request, started_at: float = 0.0) -> HttpRequestContext:
    user_agent = request.headers.get("user-agent")
    return HttpRequestContext(
        ip=client_ip(request),
        user_agent=(user_agent or "")[:255] or None,
        started_at=started_at,
    )


def request_duration_ms() -> int:
    """当前请求已耗时（毫秒）。无请求上下文（后台任务）返回 0，调用方按需处理。

    供 ``log_operation`` 等审计写入推导真实耗时，替代此前各处硬编码 ``duration_ms=0``。
    """
    started = _request_context.get().started_at
    return int((time.perf_counter() - started) * 1000) if started else 0


def bind(context: HttpRequestContext):  # type: ignore[no-untyped-def]
    return _request_context.set(context)


def reset(token) -> None:  # type: ignore[no-untyped-def]
    _request_context.reset(token)


def current_context() -> HttpRequestContext:
    return _request_context.get()
