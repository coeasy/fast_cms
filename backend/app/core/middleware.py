"""兼容 shim：请求上下文与操作日志中间件已上移到 ``app/kernel/middleware.py``。

**为什么搬**：``OperationLogMiddleware`` 直接写 ``OperationLog`` 表（并且自己开
``async_session_factory()``），属于有状态的底座；``RequestContextMiddleware`` 与它共用
request-id / 慢请求判定的同一套逻辑，拆开反而要再引一次跨层依赖，所以两个一起走。

下一轮删除本文件，把导入路径统一改成 ``app.kernel.middleware``。
"""

from __future__ import annotations

from app.kernel.middleware import (
    CONTENT_SECURITY_POLICY,
    PERMISSIONS_POLICY,
    OperationLogMiddleware,
    RequestContextMiddleware,
)

__all__ = [
    "CONTENT_SECURITY_POLICY",
    "PERMISSIONS_POLICY",
    "OperationLogMiddleware",
    "RequestContextMiddleware",
]
