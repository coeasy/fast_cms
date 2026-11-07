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
    hash_password_async,
    hash_token,
    new_refresh_token,
    new_session_id,
    validate_password_strength,
    verify_login_password_async,
    verify_password_async,
)
from app.core.serializers import iso
from app.db.base import rowcount, utcnow
from app.kernel.db import commit
from app.kernel.models import Role, User, UserSession
from app.kernel.serializers import user_out
from app.modules.sys.auth.schemas import ChangePasswordRequest
from app.modules.sys.config.service import password_min_length
from app.modules.sys.security.service import SERVICE_ACCOUNT_PREFIX
from app.modules.sys.system.service import write_login_log

ACCESS_EXPIRES_SECONDS = settings.auth_access_ttl_min * 60
# 并发刷新的宽限窗口。多个标签页同时用同一个 refresh token 换票时，条件更新
# 只允许一方命中；另一方拿到的是「上一代」token。如果直接按重放处理并撤销
# 会话，用户开两个标签页就会被踢下线——而真正的 token 重放通常发生在远大于
# 这个窗口之后。窗口内的第二次轮换视为并发，窗口外的复用仍然判定为重放。
REFRESH_REUSE_GRACE_SECONDS = 30


def _load_user_statement(username_normalized: str):    return (
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
        raise AppError("AUTH_SERVICE_ACCOUNT_LOGIN", status_code=403)
    user = (await db.execute(_load_user_statement(normalized))).scalars().first()
    now = utcnow()

    password_ok = await verify_login_password_async(password, user.password_hash if user else None)
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
            await commit(db)
        await write_login_log(
            db,
            username=username,
            user_id=user.id if user else None,
            status="failure",
            reason="invalid_credentials",
            ip=ip,
            user_agent=user_agent,
        )
        raise AppError("AUTH_INVALID_CREDENTIALS", status_code=401)

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
        raise AppError("AUTH_ACCOUNT_LOCKED", status_code=423)

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
        raise AppError("AUTH_USER_DISABLED", "账号已被禁用", 401)

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
        raise AppError("AUTH_REFRESH_INVALID", status_code=401)
    now = utcnow()
    grace_reuse = session.previous_refresh_token_hash == token_hash
    if grace_reuse:
        within_grace = (
            session.last_seen_at is not None
            and now - session.last_seen_at <= timedelta(seconds=REFRESH_REUSE_GRACE_SECONDS)
        )
        if session.revoked_at is not None or not within_grace:
            session.revoked_at = now
            await commit(db)
            raise AppError("AUTH_REFRESH_REUSED", status_code=401)
    if session.revoked_at is not None or session.expires_at <= now:
        raise AppError("AUTH_SESSION_EXPIRED", "会话已过期，请重新登录", 401)

    user = (await db.execute(_load_user_statement_by_id(session.user_id))).scalars().first()
    if user is None or user.status != "active":
        session.revoked_at = now
        await commit(db)
        raise AppError("AUTH_USER_DISABLED", "账号不存在或已被禁用", 401)

    # 先把要用的标量快照下来。下面的 rowcount==0 分支会 rollback，而 rollback
    # 会把 session/user 实例全部置为 expired；之后再读 user.id 这类属性会触发
    # 同步懒加载（MissingGreenlet），在 async 会话里是硬错误。
    session_id = session.id
    user_id = user.id
    token_version = user.token_version
    must_change = user.must_change_password
    user_payload = user_out(user)

    if grace_reuse:
        # 并发刷新：另一个标签页刚刚轮换过，当前 cookie 里已经是新一代 token。
        # 这里**不再轮换**——再换一次会把先到那一方手里的新 token 变成废票，
        # 结果是两个标签页轮流把对方踢掉。只补发 access token，cookie 保持不动。
        return {
            "access_token": create_access_token(user_id, session_id, token_version),
            "token_type": "bearer",
            "expires_in": ACCESS_EXPIRES_SECONDS,
            "must_change_password": must_change,
            "user": user_payload,
        }

    new_token = new_refresh_token()
    new_hash = hash_token(new_token)
    # 条件更新即轮换锁：并发刷新只有一方能命中，另一方走上面的宽限分支
    rotated = await db.execute(
        update(UserSession)
        .where(
            UserSession.id == session_id,
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
        # SELECT 与 UPDATE 之间被另一个并发刷新抢先轮换了：此时本轮持有的正是
        # 「上一代」token，按并发处理补发 access token，而不是把用户踢下线。
        await db.rollback()
        current = await db.get(UserSession, session_id)
        if (
            current is not None
            and current.revoked_at is None
            and current.previous_refresh_token_hash == token_hash
            and current.last_seen_at is not None
            and now - current.last_seen_at <= timedelta(seconds=REFRESH_REUSE_GRACE_SECONDS)
        ):
            return {
                "access_token": create_access_token(user_id, current.id, token_version),
                "token_type": "bearer",
                "expires_in": ACCESS_EXPIRES_SECONDS,
                "must_change_password": must_change,
                "user": user_payload,
            }
        raise AppError("AUTH_REFRESH_INVALID", status_code=401)
    await commit(db)
    return {
        "access_token": create_access_token(user_id, session_id, token_version),
        "token_type": "bearer",
        "expires_in": ACCESS_EXPIRES_SECONDS,
        "refresh_token": new_token,
        "must_change_password": must_change,
        "user": user_payload,
    }


def _load_user_statement_by_id(user_id: int):    return (
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
    if not await verify_password_async(data.old_password, user.password_hash):
        raise AppError("AUTH_OLD_PASSWORD_INVALID", status_code=400)
    valid, message = validate_password_strength(
        data.new_password, user.username, min_length=await password_min_length(db)
    )
    if not valid:
        raise AppError("AUTH_WEAK_PASSWORD", message, 422)
    if data.old_password == data.new_password:
        raise AppError("AUTH_PASSWORD_UNCHANGED", status_code=422)
    user.password_hash = await hash_password_async(data.new_password)
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
    await commit(db)
    access_token = create_access_token(user.id, session.id, user.token_version)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_EXPIRES_SECONDS,
        "user": user_out(user),
    }


async def logout(db: AsyncSession, session: UserSession) -> None:
    session.revoked_at = utcnow()
    await commit(db)


async def touch_session(db: AsyncSession, session: UserSession) -> None:
    session.last_seen_at = utcnow()
    await commit(db)
