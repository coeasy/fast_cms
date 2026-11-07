"""内核级数据范围工具：用户可见范围判定与会话撤销。

这些逻辑同时被 ``system``（用户管理）、``auth``（登录/改密）、``security``
（API Key 绑定账号）使用，属于平台级能力而非某个模块的私有实现。
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.base import utcnow
from app.kernel.authz import AuthContext, DataScope, get_data_scope, user_in_scope
from app.kernel.models import User, UserSession


async def user_scope(db: AsyncSession, ctx: AuthContext) -> DataScope:
    return await get_data_scope(db, ctx)


async def can_access_user(db: AsyncSession, ctx: AuthContext, user: User) -> bool:
    return user_in_scope(ctx, await user_scope(db, ctx), user)


async def ensure_user_access(db: AsyncSession, ctx: AuthContext, user: User | None) -> User:
    """校验目标用户是否在当前角色的数据范围内，越权直接 403。"""
    if user is None:
        raise AppError("SYSTEM_USER_NOT_FOUND", status_code=404)
    if not await can_access_user(db, ctx, user):
        raise AppError("SYSTEM_USER_FORBIDDEN", "无权操作该用户", 403)
    return user


async def revoke_user_sessions(
    db: AsyncSession, user_id: int, *, keep_id: str | None = None
) -> None:
    """撤销用户全部未失效会话；``keep_id`` 用于保留当前登录（如自助改密）。"""
    statement = update(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
    )
    if keep_id:
        statement = statement.where(UserSession.id != keep_id)
    await db.execute(statement.values(revoked_at=utcnow()))
