from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute


class _PermissionEndpoint(Protocol):
    """带 ``__permission__`` 标记的端点函数（装饰器写入、路由读取）。"""

    __permission__: str


class PermissionAPIRoute(APIRoute):
    def __init__(self, path: str, endpoint: Any, *, permission: str | None = None, **kwargs: Any) -> None:
        permission = permission or getattr(endpoint, "__permission__", None)
        if permission:
            dependencies = list(kwargs.get("dependencies") or [])
            from app.core.authz import require_permission

            dependencies.append(Depends(require_permission(permission)))
            kwargs["dependencies"] = dependencies
            extra = dict(kwargs.get("openapi_extra") or {})
            extra["x-permission"] = permission
            kwargs["openapi_extra"] = extra
        super().__init__(path, endpoint, **kwargs)


class PermissionAPIRouter(APIRouter):
    def _decorator(
        self,
        method: str,
        path: str,
        *,
        permission: str | None = None,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if "response_model" in kwargs:
            # 服务层的 ``*_out`` 会按需省略字段（如列表不带 total、详情才带 script_code）。
            # 不排除未设置字段的话，Pydantic 会把它们补成 null 输出，等于把
            # 「没有这个字段」变成「字段是 null」，前端和契约都要跟着改。
            kwargs.setdefault("response_model_exclude_unset", True)
        decorator = getattr(super(), method)(path, **kwargs)

        def outer(endpoint: Callable[..., Any]) -> Callable[..., Any]:
            if permission:
                # 端点函数本无 __permission__ 属性；先 cast 到声明该属性的协议再赋值，
                # 既满足 mypy（属性存在）又不触发 ruff B010（不用 setattr）
                cast(_PermissionEndpoint, endpoint).__permission__ = permission
            return decorator(endpoint)

        return outer

    # 收窄为 permission 版签名是本类的核心用途，与基类签名差异是刻意设计
    def get(  # type: ignore[override]
        self, path: str, *, permission: str | None = None, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._decorator("get", path, permission=permission, **kwargs)

    def post(  # type: ignore[override]
        self, path: str, *, permission: str | None = None, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._decorator("post", path, permission=permission, **kwargs)

    def put(  # type: ignore[override]
        self, path: str, *, permission: str | None = None, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._decorator("put", path, permission=permission, **kwargs)

    def patch(  # type: ignore[override]
        self, path: str, *, permission: str | None = None, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._decorator("patch", path, permission=permission, **kwargs)

    def delete(  # type: ignore[override]
        self, path: str, *, permission: str | None = None, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._decorator("delete", path, permission=permission, **kwargs)


def protected_router(**kwargs: Any) -> PermissionAPIRouter:
    return PermissionAPIRouter(route_class=PermissionAPIRoute, **kwargs)
