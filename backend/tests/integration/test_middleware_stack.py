"""B-3 / Phase 12-1：中间件栈序断言。

``assembly.register_middleware`` 的注释声称的实际栈序（外 → 内）
``RequestContext → IpGuard → RateLimit → OperationLog → CORS`` 此前与旧注释
「IP 守卫必须最外层」相反——注释靠人维护，必然腐化。这里直接遍历
``app.build_middleware_stack()`` 锁死真实顺序，改注册顺序必先红测试。
"""

from __future__ import annotations

from fastapi import FastAPI

# 本项目注册的业务中间件（按期望的外 → 内顺序）
EXPECTED_ORDER = [
    "RequestContextMiddleware",
    "IpGuardMiddleware",
    "RateLimitMiddleware",
    "OperationLogMiddleware",
    "CORSMiddleware",
]


def _business_middleware_names(app: FastAPI) -> list[str]:
    """从最外层到最内层，只取本项目注册的中间件类名。

    栈在首次请求时才真正装配，这里显式调 ``build_middleware_stack()``
    在不发请求的前提下拿到同一份顺序；Starlette/FastAPI 自己包的
    ServerError / ExceptionTelemetry / ExceptionMiddleware 等层过滤掉。
    """
    names: list[str] = []
    node = app.build_middleware_stack()
    while node is not None:
        name = type(node).__name__
        if name in EXPECTED_ORDER:
            names.append(name)
        node = getattr(node, "app", None)
    return names


def test_middleware_stack_order() -> None:
    from app.core.config import settings
    from app.main import create_app

    expected = EXPECTED_ORDER if settings.cors_origins else EXPECTED_ORDER[:-1]
    actual = _business_middleware_names(create_app())
    assert actual == expected, f"中间件栈序漂移：{actual}（期望 {expected}）"
