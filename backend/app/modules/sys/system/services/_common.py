"""system 模块内部的共享工具。

通用能力（提交、操作日志、数据范围、会话撤销）已下沉到 ``app/kernel``，
这里只保留系统管理自己的校验与裁剪逻辑，并把内核能力以下划线别名 re-export，
避免改动既有 ``from ._common import _commit`` 之类的调用点。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core import ipgeo
from app.core.authz import AuthContext
from app.core.errors import AppError
from app.kernel.db import commit as _commit
from app.kernel.db import log_operation
from app.kernel.models import User
from app.kernel.scope import can_access_user as _can_access_user
from app.kernel.scope import ensure_user_access as _ensure_user_access
from app.kernel.scope import revoke_user_sessions
from app.kernel.scope import revoke_user_sessions as _revoke_sessions
from app.kernel.scope import user_scope as _user_scope
from app.kernel.serializers import user_out

__all__ = [
    "LOGIN_IP_PERMISSION",
    "MAX_DEPT_ANCESTORS_LEN",
    "MAX_DEPT_DEPTH",
    "USERNAME_ADMIN",
    "_can_access_user",
    "_commit",
    "_ensure_can_manage_superuser",
    "_ensure_user_access",
    "_log_operation",
    "_normalize_username",
    "_revoke_sessions",
    "_user_payload",
    "_user_scope",
    "_validate_dept_path",
    "ensure_user_access",
    "log_operation",
    "revoke_user_sessions",
]

MAX_DEPT_ANCESTORS_LEN = 500


MAX_DEPT_DEPTH = 50


def _validate_dept_path(ancestors: str) -> None:
    """校验部门物化路径长度与深度；超限抛出明确错误（P1-7）。"""
    if len(ancestors) > MAX_DEPT_ANCESTORS_LEN:
        raise AppError(
            "SYSTEM_DEPT_TOO_DEEP",
            f"部门层级过深：路径长度 {len(ancestors)} 超过上限 {MAX_DEPT_ANCESTORS_LEN}",
            422,
        )
    depth = ancestors.count("/") if ancestors else 0
    if depth > MAX_DEPT_DEPTH:
        raise AppError(
            "SYSTEM_DEPT_TOO_DEEP",
            f"部门层级过深：深度 {depth} 超过上限 {MAX_DEPT_DEPTH}",
            422,
        )


USERNAME_ADMIN = "admin"


LOGIN_IP_PERMISSION = "monitor:login-log:list"


def _user_payload(
    ctx: AuthContext,
    user: User,
    locations: Mapping[str, ipgeo.IpLocation | None] | None = None,
) -> dict[str, Any]:
    data = user_out(user, locations=locations)
    if not ctx.has(LOGIN_IP_PERMISSION):
        # 归属地和 IP 是同一份信息的两种形态，遮 IP 不遮归属地等于没遮
        data["last_login_ip"] = None
        data["last_login_location"] = None
    return data


def _normalize_username(value: str) -> str:
    return value.strip().lower()


def _ensure_can_manage_superuser(ctx: AuthContext, target_superuser: bool) -> None:
    if target_superuser and not ctx.is_superuser:
        raise AppError("SYSTEM_USER_FORBIDDEN", "仅超级管理员可操作超级管理员账号", 403)


async def _log_operation(
    db,
    ctx: AuthContext,
    *,
    module: str,
    action: str,
    detail: str | None = None,
    method: str = "POST",
    path: str = "",
) -> None:
    """兼容别名：内核 ``log_operation`` 的下划线版本。"""
    await log_operation(
        db, ctx, module=module, action=action, detail=detail, method=method, path=path
    )


async def ensure_user_access(db, ctx: AuthContext, user: User | None) -> User:
    """校验目标用户是否在当前角色的数据范围内，越权直接 403。"""
    return await _ensure_user_access(db, ctx, user)
