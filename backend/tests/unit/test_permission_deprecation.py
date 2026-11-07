"""6-2：权限废弃的运行期行为（宽限期放行 + Deprecation/Sunset 头 + 到期拒绝）。

权限被标记 ``deprecated_at`` 后，仍持有它的用户在宽限期内可以继续访问，
响应带标准 ``Deprecation`` / ``Sunset`` 头提示迁移；超过宽限期则拒绝。
这里直接调用 ``require_permission`` 依赖，验证四条分支，不依赖数据库。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.core.authz import AuthContext, require_permission
from app.core.config import settings
from app.core.errors import AppError


def _ctx(*, permissions: set[str] | None = None, deprecated: dict[str, datetime] | None = None) -> AuthContext:
    return AuthContext(
        user=SimpleNamespace(is_superuser=False),  # type: ignore[arg-type]
        session=None,
        permissions=set(permissions or ()),
        deprecated=dict(deprecated or {}),
    )


async def test_active_permission_passes_without_headers() -> None:
    dep = require_permission("system:theme:list")
    resp = Response()
    ctx = _ctx(permissions={"system:theme:list"})
    assert await dep(response=resp, ctx=ctx) is ctx
    assert "Deprecation" not in resp.headers
    assert "Sunset" not in resp.headers


async def test_deprecated_within_grace_is_allowed_with_headers() -> None:
    dep = require_permission("system:theme:list")
    resp = Response()
    ctx = _ctx(deprecated={"system:theme:list": datetime.now(UTC) - timedelta(days=1)})
    assert await dep(response=resp, ctx=ctx) is ctx
    assert resp.headers["Deprecation"] == "true"
    sunset = resp.headers["Sunset"]
    assert "GMT" in sunset  # RFC 7231 HTTP-date
    assert sunset.endswith("GMT")


async def test_deprecated_within_grace_keeps_header_even_if_still_granted() -> None:
    """宽限期内权限仍留在 permissions 里（菜单/按钮照常），头也必须照发。

    这是回归点：若 ``require_permission`` 先判 ``has()`` 再判废弃，权限还在
    permissions 里就会提前 return，Deprecation/Sunset 头永远发不出去。
    """
    dep = require_permission("system:theme:list")
    resp = Response()
    ctx = _ctx(
        permissions={"system:theme:list"},
        deprecated={"system:theme:list": datetime.now(UTC) - timedelta(days=1)},
    )
    assert ctx.has("system:theme:list") is True
    assert await dep(response=resp, ctx=ctx) is ctx
    assert resp.headers["Deprecation"] == "true"
    assert "Sunset" in resp.headers


async def test_deprecated_past_sunset_is_denied() -> None:
    dep = require_permission("system:theme:list")
    past = datetime.now(UTC) - timedelta(days=settings.permission_deprecation_grace_days + 1)
    ctx = _ctx(deprecated={"system:theme:list": past})
    with pytest.raises(AppError) as excinfo:
        await dep(response=Response(), ctx=ctx)
    assert excinfo.value.code == "AUTH_PERMISSION_SUNSET"
    assert excinfo.value.status_code == 403


async def test_missing_permission_is_forbidden() -> None:
    dep = require_permission("system:theme:list")
    with pytest.raises(AppError) as excinfo:
        await dep(response=Response(), ctx=_ctx())
    assert excinfo.value.code == "AUTH_FORBIDDEN"
    assert excinfo.value.status_code == 403


async def test_superuser_bypasses_deprecation() -> None:
    dep = require_permission("system:theme:list")
    resp = Response()
    ctx = AuthContext(user=SimpleNamespace(is_superuser=True), session=None)  # type: ignore[arg-type]
    assert await dep(response=resp, ctx=ctx) is ctx
    assert "Deprecation" not in resp.headers


def test_within_grace_boundaries() -> None:
    """宽限期判定：标记当天算在宽限期内，超过 grace_days 才算过期。"""
    from app.core.authz import _within_grace

    grace = settings.permission_deprecation_grace_days
    assert _within_grace(datetime.now(UTC)) is True
    assert _within_grace(datetime.now(UTC) - timedelta(days=grace - 1)) is True
    assert _within_grace(datetime.now(UTC) - timedelta(days=grace + 1)) is False


async def test_superuser_ignores_expired_deprecation() -> None:
    """超管即使角色里挂着已过期权限，也不能被挡：它走的是「绕过权限判断」。"""
    dep = require_permission("system:theme:list")
    resp = Response()
    past = datetime.now(UTC) - timedelta(days=settings.permission_deprecation_grace_days + 1)
    ctx = AuthContext(
        user=SimpleNamespace(is_superuser=True),  # type: ignore[arg-type]
        session=None,
        deprecated={"system:theme:list": past},
    )
    assert await dep(response=resp, ctx=ctx) is ctx
    assert "Sunset" not in resp.headers


async def test_wildcard_api_key_ignores_expired_deprecation() -> None:
    """API Key 的 ``*`` 同理：不因为某个码过期而整体失效。"""
    dep = require_permission("system:theme:list")
    resp = Response()
    past = datetime.now(UTC) - timedelta(days=settings.permission_deprecation_grace_days + 1)
    ctx = AuthContext(
        user=SimpleNamespace(is_superuser=False),  # type: ignore[arg-type]
        session=None,
        deprecated={"system:theme:list": past},
        all_permissions=True,
    )
    assert await dep(response=resp, ctx=ctx) is ctx
