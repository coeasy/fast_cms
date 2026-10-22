from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute


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
        decorator = getattr(super(), method)(path, **kwargs)

        def outer(endpoint: Callable[..., Any]) -> Callable[..., Any]:
            if permission:
                setattr(endpoint, "__permission__", permission)
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
