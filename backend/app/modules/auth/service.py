from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    new_session_id,
    validate_password_strength,
    verify_login_password,
    verify_password,
)
from app.core.serializers import iso
from app.db.base import rowcount, utcnow
from app.modules.auth.schemas import ChangePasswordRequest
from app.modules.config.service import password_min_length
from app.modules.security.service import SERVICE_ACCOUNT_PREFIX
from app.modules.system.models import Role, User, UserSession
from app.modules.system.serializers import user_out
from app.modules.system.service import write_login_log

ACCESS_EXPIRES_SECONDS = settings.auth_access_ttl_min * 60


def _load_user_statement(username_normalized: str):  # type: ignore[no-untyped-def]
    return (
        select(User)
        .options(
            selectinload(User.dept),
            selectinload(User.roles).selectinload(Role.permissions),
            selectinload(User.roles).selectinload(Role.departments),
        )
        .where(User.username_normalized == username_normalized)
    )


async def login(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    normalized = username.strip().lower()
    if normalized.startswith(SERVICE_ACCOUNT_PREFIX):
        # 服务账号只服务 API Key。口令虽然是随机的，但显式拒绝能让日志里
        # 留下「有人拿服务账号试口令」的痕迹，而不是一次普通的密码错误。
        await write_login_log(
            db,
            username=username,
            status="failure",
            reason="service_account_login_blocked",
            ip=ip,
            user_agent=user_agent,
        )
        raise AppError("AUTH_SERVICE_ACCOUNT_LOGIN", "服务账号不能直接登录，请使用 API Key", 403)
    user = (await db.execute(_load_user_statement(normalized))).scalars().first()
    now = utcnow()

    password_ok = verify_login_password(password, user.password_hash if user else None)
    if not password_ok:
        if user:
            # 并发失败登录必须用 SQL 端自增，ORM 读改写会少计，导致锁定阈值被绕过
            await db.execute(
                update(User)
                .where(User.id == user.id)
                .values(failed_login_count=User.failed_login_count + 1)
            )
            failed_count = int(
                (
                    await db.execute(
                        select(User.failed_login_count).where(User.id == user.id)
                    )
                ).scalar_one()
            )
            if failed_count >= settings.auth_max_failed:
                await db.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(locked_until=now + timedelta(minutes=settings.auth_lock_minutes))
                )
            await db.commit()
        await write_login_log(
            db,
            username=username,
            user_id=user.id if user else None,
            status="failure",
            reason="invalid_credentials",
            ip=ip,
            user_agent=user_agent,
        )
        raise AppError("AUTH_INVALID_CREDENTIALS", "用户名或密码错误", 401)

    assert user is not None
    if user.locked_until and user.locked_until > now:
        await write_login_log(
            db,
            username=username,
            user_id=user.id,
            status="failure",
            reason="account_locked",
            ip=ip,
            user_agent=user_agent,
        )
        raise AppError("AUTH_ACCOUNT_LOCKED", "账号已锁定，请稍后再试", 423)

    if user.is_banned:
        # 封禁与锁定分开报：锁定是防爆破的临时措施，封禁是管理员的明确处置，
        # 把原因和解封时间回给前端，用户才知道该找谁、要等多久
        await write_login_log(
            db,
            username=username,
            user_id=user.id,
            status="failure",
            reason="account_banned",
            ip=ip,
            user_agent=user_agent,
        )
        raise AppError(
            "AUTH_ACCOUNT_BANNED",
            "账号已被封禁",
            403,
            {
                "reason": user.ban_reason,
                "banned_at": iso(user.banned_at) if user.banned_at else None,
                "until": iso(user.banned_until) if user.banned_until else None,
                "permanent": user.banned_until is None,
            },
        )

    if user.status != "active":
        await write_login_log(
            db,
            username=username,
            user_id=user.id,
            status="failure",
            reason="user_disabled",
            ip=ip,
            user_agent=user_agent,
        )
        raise AppError("AUTH_USER_DISABLED", "账号已被禁用", 403)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip

    refresh_token = new_refresh_token()
    session = UserSession(
        id=new_session_id(),
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        expires_at=now + timedelta(days=settings.auth_refresh_ttl_days),
        last_seen_at=now,
        created_at=now,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(session)
    await write_login_log(
        db,
        username=username,
        user_id=user.id,
        status="success",
        reason=None,
        ip=ip,
        user_agent=user_agent,
    )
    access_token = create_access_token(user.id, session.id, user.token_version)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_EXPIRES_SECONDS,
        "refresh_token": refresh_token,
        "must_change_password": user.must_change_password,
        "user": user_out(user),
    }


async def refresh_session(
    db: AsyncSession,
    *,
    refresh_token: str,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    token_hash = hash_token(refresh_token)
    session = (
        (
            await db.execute(
                select(UserSession).where(
                    (UserSession.refresh_token_hash == token_hash)
                    | (UserSession.previous_refresh_token_hash == token_hash)
                )
            )
        )
        .scalars()
        .first()
    )
    if session is None:
        raise AppError("AUTH_REFRESH_INVALID", "刷新凭证无效，请重新登录", 401)
    if session.previous_refresh_token_hash == token_hash:
        session.revoked_at = utcnow()
        await db.commit()
        raise AppError("AUTH_REFRESH_REUSED", "检测到刷新凭证重复使用，会话已撤销", 401)
    now = utcnow()
    if session.revoked_at is not None or session.expires_at <= now:
        raise AppError("AUTH_SESSION_EXPIRED", "会话已过期，请重新登录", 401)

    user = (await db.execute(_load_user_statement_by_id(session.user_id))).scalars().first()
    if user is None or user.status != "active":
        session.revoked_at = now
        await db.commit()
        raise AppError("AUTH_USER_DISABLED", "账号不存在或已被禁用", 401)

    new_token = new_refresh_token()
    new_hash = hash_token(new_token)
    # 条件更新即轮换锁：并发刷新只有一方能命中，另一方按无效凭证处理，不会互相顶掉
    rotated = await db.execute(
        update(UserSession)
        .where(
            UserSession.id == session.id,
            UserSession.refresh_token_hash == token_hash,
            UserSession.revoked_at.is_(None),
        )
        .values(
            previous_refresh_token_hash=token_hash,
            refresh_token_hash=new_hash,
            last_seen_at=now,
            ip=ip or session.ip,
            user_agent=(user_agent or "")[:255] or session.user_agent,
        )
    )
    if not rowcount(rotated):
        await db.rollback()
        raise AppError("AUTH_REFRESH_INVALID", "刷新凭证无效，请重新登录", 401)
    await db.commit()
    access_token = create_access_token(user.id, session.id, user.token_version)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_EXPIRES_SECONDS,
        "refresh_token": new_token,
        "must_change_password": user.must_change_password,
        "user": user_out(user),
    }


def _load_user_statement_by_id(user_id: int):  # type: ignore[no-untyped-def]
    return (
        select(User)
        .options(
            selectinload(User.dept),
            selectinload(User.roles).selectinload(Role.permissions),
            selectinload(User.roles).selectinload(Role.departments),
        )
        .where(User.id == user_id)
    )


async def change_password(
    db: AsyncSession,
    *,
    user: User,
    session: UserSession,
    data: ChangePasswordRequest,
) -> dict[str, Any]:
    if not verify_password(data.old_password, user.password_hash):
        raise AppError("AUTH_OLD_PASSWORD_INVALID", "当前密码不正确", 400)
    valid, message = validate_password_strength(
        data.new_password, user.username, min_length=await password_min_length(db)
    )
    if not valid:
        raise AppError("AUTH_WEAK_PASSWORD", message, 422)
    if data.old_password == data.new_password:
        raise AppError("AUTH_PASSWORD_UNCHANGED", "新密码不能与当前密码相同", 422)
    user.password_hash = hash_password(data.new_password)
    user.must_change_password = False
    user.token_version += 1
    await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.id != session.id,
        )
        .values(revoked_at=utcnow())
    )
    await db.commit()
    access_token = create_access_token(user.id, session.id, user.token_version)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_EXPIRES_SECONDS,
        "user": user_out(user),
    }


async def logout(db: AsyncSession, session: UserSession) -> None:
    session.revoked_at = utcnow()
    await db.commit()


async def touch_session(db: AsyncSession, session: UserSession) -> None:
    session.last_seen_at = utcnow()
    await db.commit()
