"""客户端地址解析：ASGI scope 与 ``Request`` 两个入口共用同一套判定。

单独成模块是因为它被三层同时需要：``kernel/middleware``（纯 ASGI，只有 scope）、
``kernel/ipguard``（同上）、``kernel/authz``（有 ``Request``）。如果留在 ``authz`` 里，
``core/request_context`` 为了拿一个纯函数就得反向 import ``app.kernel``，
把刚建立的「kernel → core 单向」又破坏掉。这里没有任何 ORM 依赖，天然属于 core。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.config import settings


def client_ip_from_scope(scope: Any) -> str | None:
    """从 ASGI scope 里解析客户端 IP。

    中间件改纯 ASGI 后拿不到 ``Request`` 对象，抽一个 scope 版本共用同一套判定：
    uvicorn 只有在直连来源属于 FORWARDED_ALLOW_IPS 时才用 X-Forwarded-For 改写
    scope["client"]，所以默认只信任 peer；显式开启 TRUST_PROXY 后才采用原始 XFF，
    否则客户端可伪造该头绕过登录限流并污染审计日志。
    """
    if settings.trust_proxy:
        for key, value in scope.get("headers") or ():
            if key == b"x-forwarded-for":
                first = value.decode("latin-1").split(",")[0].strip()
                if first:
                    return first[:64]
                break
    client = scope.get("client") or ()
    peer = client[0] if client else None
    return peer[:64] if peer else None


def client_ip(request: Request) -> str | None:
    return client_ip_from_scope(request.scope)
