from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Request

from app.core.authz import client_ip


@dataclass(frozen=True)
class HttpRequestContext:
    ip: str | None = None
    user_agent: str | None = None


EMPTY = HttpRequestContext()

_request_context: ContextVar[HttpRequestContext] = ContextVar("http_request_context", default=EMPTY)


def build_context(request: Request) -> HttpRequestContext:
    user_agent = request.headers.get("user-agent")
    return HttpRequestContext(ip=client_ip(request), user_agent=(user_agent or "")[:255] or None)


def bind(context: HttpRequestContext):  # type: ignore[no-untyped-def]
    return _request_context.set(context)


def reset(token) -> None:  # type: ignore[no-untyped-def]
    _request_context.reset(token)


def current_context() -> HttpRequestContext:
    return _request_context.get()
