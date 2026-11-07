from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.envelope import CSV_RESPONSES
from app.core.export import MAX_EXPORT_ROWS
from app.core.routing import _PermissionEndpoint, protected_router
from app.db.session import get_db
from app.modules.ops.export import service

router = protected_router(
    prefix="/export",
    tags=["数据导出"],
    # 与其它管理端路由保持一致：导出属于后台能力，光有某个对象的
    # `*:export` 权限码不足以进入后台。缺这一层会出现「不能登录后台的
    # 角色却能拉走整份用户 CSV」的越权边界。
    dependencies=[Depends(require_admin)],
)


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
        # 上限与 core/export.MAX_EXPORT_ROWS 单源：路由参数、服务常量、响应头、文档引用同一来源
        limit: int = Query(default=service.EXPORT_LIMIT, ge=1, le=MAX_EXPORT_ROWS),
        ctx: AuthContext = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Any:
        response, _count, _truncated = await service.export(
            db, ctx, spec_key, keyword=keyword, status=status, limit=limit
        )
        # csv_response 已在响应头写入 X-Export-Rows / X-Export-Limit / X-Export-Truncated，
        # 调用方不读 body 也能知道是不是导全了
        return response

    endpoint.__name__ = f"export_{spec_key.replace('-', '_')}"
    # 端点函数本无 __permission__ 属性；cast 到声明该属性的协议再赋值（同 core/routing）
    cast(_PermissionEndpoint, endpoint).__permission__ = _spec_permission
    return endpoint


for _spec_key, _spec in service.SPECS.items():
    _spec_permission = _spec.permission
    router.add_api_route(
        f"/{_spec_key}",
        _build_endpoint(_spec_key),
        methods=["GET"],
        summary=f"导出{_spec.title}",
        responses=CSV_RESPONSES,
    )
