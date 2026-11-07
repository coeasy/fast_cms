from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ipgeo
from app.core.authz import AuthContext, user_in_scope
from app.core.errors import AppError
from app.core.upload import is_safe_image_content_type
from app.db.base import rowcount, utcnow
from app.kernel.db import commit as _commit
from app.kernel.models import FileObject, LoginLog, User, UserSession
from app.kernel.scope import user_scope as _user_scope
from app.kernel.serializers import session_out, user_out
from app.modules.sys.system.schemas import ProfileUpdate


async def update_profile(db: AsyncSession, ctx: AuthContext, data: ProfileUpdate) -> dict[str, Any]:
    user = ctx.user
    values = data.model_dump(exclude_unset=True)
    avatar_file_id = values.get("avatar_file_id")
    if avatar_file_id is not None:
        item = await db.get(FileObject, avatar_file_id)
        if item is None or item.uploader_id != user.id:
            raise AppError("SYSTEM_PROFILE_AVATAR_INVALID", "头像文件不存在", 422)
        if not is_safe_image_content_type(item.content_type):
            raise AppError("SYSTEM_PROFILE_AVATAR_INVALID", "头像只能是 PNG/JPEG/GIF/WebP/BMP 图片", 422)
    for field, value in values.items():
        setattr(user, field, value)
    await _commit(db)
    locations = await ipgeo.locate_many([user.last_login_ip])
    return user_out(user, locations=locations)


async def list_user_sessions(db: AsyncSession, ctx: AuthContext) -> list[dict[str, Any]]:
    sessions = (
        (
            await db.execute(
                select(UserSession)
                .where(
                    UserSession.user_id == ctx.user.id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > utcnow(),
                )
                .order_by(UserSession.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        session_out(item, current=item.id == (ctx.session.id if ctx.session is not None else None))
        for item in sessions
    ]


async def revoke_user_session(db: AsyncSession, ctx: AuthContext, session_id: str) -> None:
    session = await db.get(UserSession, session_id)
    if session is None or session.user_id != ctx.user.id:
        raise AppError("AUTH_SESSION_NOT_FOUND", "会话不存在", 404)
    session.revoked_at = utcnow()
    await _commit(db)


async def revoke_other_sessions(db: AsyncSession, ctx: AuthContext) -> int:
    if ctx.session is None:
        # API Key 身份没有会话可吊销；它自己的处置手段是停用 Key
        raise AppError("AUTH_SESSION_NOT_FOUND", "当前身份没有会话", 404)
    result = await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == ctx.user.id,
            UserSession.revoked_at.is_(None),
            UserSession.id != ctx.session.id,
        )
        .values(revoked_at=utcnow())
    )
    await _commit(db)
    return rowcount(result)


async def cleanup_old_sessions(db: AsyncSession, days: int) -> int:
    cutoff = utcnow() - timedelta(days=max(days, 1))
    result = await db.execute(delete(UserSession).where(UserSession.expires_at < cutoff))
    await _commit(db)
    return rowcount(result)


async def write_login_log(
    db: AsyncSession,
    *,
    username: str,
    status: str,
    reason: str | None,
    ip: str | None,
    user_agent: str | None,
    user_id: int | None = None,
) -> None:
    # 归属地在登录这一刻定型：之后换 xdb 不会改写已经发生的记录。
    # 库缺失或 IP 非法时 location 为 NULL，界面显示「未知」，不影响登录本身。
    location: str | None = None
    if ip:
        found = await ipgeo.locate(ip)
        location = found.label if found is not None else None
    db.add(
        LoginLog(
            username=username[:64],
            user_id=user_id,
            status=status,
            reason=reason,
            ip=ip,
            location=(location or "")[:128] or None,
            user_agent=(user_agent or "")[:255] or None,
        )
    )
    await _commit(db)


async def kick_session(db: AsyncSession, ctx: AuthContext, session_id: str) -> None:
    session = await db.get(UserSession, session_id)
    if session is None or session.revoked_at is not None:
        raise AppError("AUTH_SESSION_NOT_FOUND", "会话不存在或已失效", 404)
    if session.user_id != ctx.user.id:
        target = await db.get(User, session.user_id) if session.user_id else None
        if target is None:
            raise AppError("AUTH_SESSION_NOT_FOUND", "会话不存在或已失效", 404)
        if not user_in_scope(ctx, await _user_scope(db, ctx), target):
            raise AppError("MONITOR_KICK_FORBIDDEN", "无权下线该用户", 403)
        if target.is_superuser and not ctx.is_superuser:
            raise AppError("MONITOR_KICK_FORBIDDEN", "仅超级管理员可下线超级管理员", 403)
    session.revoked_at = utcnow()
    await _commit(db)
