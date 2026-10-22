from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.export import service

router = protected_router(prefix="/export", tags=["数据导出"])


def _build_endpoint(spec_key: str) -> Callable[..., Any]:
    """按对象生成导出端点。

    每个对象的权限码不同（能导出用户的人不该自动获得导出角色的能力），
    所以逐个注册而不是共用一个 permission 参数。

    权限通过端点上的 ``__permission__`` 声明：``PermissionAPIRoute`` 会读它，
    既注入 ``require_permission`` 依赖，又把权限码写进 openapi 的
    ``x-permission``。手工 ``Depends(require_permission(...))`` 只做事前校验、
    不落声明，check_contract.py 会判「受保护路由缺少 x-permission」，
    权限码也会因为关联不上任何路由被当成死配置。
    """

    async def endpoint(
        keyword: str | None = Query(default=None, max_length=64),
        status: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=service.EXPORT_LIMIT, ge=1, le=50000),
        ctx: AuthContext = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Any:
        response, count, truncated = await service.export(
            db, ctx, spec_key, keyword=keyword, status=status, limit=limit
        )
        # 截断信息放到响应头：调用方不读 body 也能知道是不是导全了
        response.headers["X-Export-Rows"] = str(count)
        response.headers["X-Export-Truncated"] = "1" if truncated else "0"
        return response

    endpoint.__name__ = f"export_{spec_key.replace('-', '_')}"
    # 用 setattr 而不是直接赋值：函数对象没有 __permission__ 属性，
    # 直接写 mypy 会报 attr-defined
    setattr(endpoint, "__permission__", _spec_permission)
    return endpoint


for _spec_key, _spec in service.SPECS.items():
    _spec_permission = _spec.permission
    router.add_api_route(
        f"/{_spec_key}",
        _build_endpoint(_spec_key),
        methods=["GET"],
        summary=f"导出{_spec.title}",
    )
