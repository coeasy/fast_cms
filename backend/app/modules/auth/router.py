from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, client_ip, get_current_user
from app.core.cache import get_cache
from app.core.config import settings
from app.core.errors import AppError, error_payload, success
from app.core.serializers import iso
from app.db.base import utcnow
from app.db.session import get_db
from app.modules.auth import service
from app.modules.auth.schemas import ChangePasswordRequest, LoginRequest
from app.modules.content import service as content_service
from app.modules.content.models import FileObject
from app.modules.system import service as system_service
from app.modules.system.schemas import ProfileUpdate
from app.modules.system.serializers import session_out, user_out

logger = logging.getLogger("fastcms.auth")

router = APIRouter(prefix="/auth", tags=["认证"])


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_refresh_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=f"{settings.api_prefix}/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path=f"{settings.api_prefix}/auth",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.post("/login", summary="登录")
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ip = client_ip(request)
    cache = get_cache()
    rate_key = f"fastcms:login-rate:{ip or 'unknown'}"
    attempts = await cache.incr(rate_key, settings.login_rate_window)
    if attempts > settings.login_rate_limit:
        await system_service.write_login_log(
            db,
            username=data.username,
            status="failure",
            reason="rate_limited",
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        raise AppError("AUTH_RATE_LIMITED", "登录尝试过于频繁，请稍后再试", 429)
    try:
        result = await service.login(
            db,
            username=data.username,
            password=data.password,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
    except AppError as exc:
        # 失败计数按 IP 聚合：账号级锁定只挡「同一个账号」，
        # 而密码喷洒是拿一个 IP 试很多账号，必须在 IP 维度兜住。
        if exc.code != "AUTH_RATE_LIMITED":
            await _note_failed_login(db, ip, exc)
        raise
    await cache.delete(rate_key)
    await cache.delete(_fail_key(ip))
    refresh_token = result.pop("refresh_token")
    _set_refresh_cookie(response, refresh_token)
    return success(result, "登录成功")


def _fail_key(ip: str | None) -> str:
    return f"fastcms:login-fail:{ip or 'unknown'}"


async def _note_failed_login(db: AsyncSession, ip: str | None, exc: AppError) -> None:
    """累计失败次数，达到阈值时自动生成一条临时封禁网段。"""
    if not ip:
        return
    cache = get_cache()
    window = max(60, settings.auth_ip_ban_minutes * 60)
    fails = await cache.incr(_fail_key(ip), window)
    if fails < settings.auth_max_failed:
        return
    from app.modules.security import service as security_service

    reason = f"登录失败 {fails} 次后自动封禁"
    try:
        await security_service.block_ip_now(
            db,
            None,
            ip=ip,
            scope="api",
            reason=reason,
            minutes=settings.auth_ip_ban_minutes,
        )
    except AppError:
        # 已有同名规则时说明封禁仍在有效期内，直接复用即可
        return
    logger.warning("自动封禁登录失败来源 IP=%s 失败次数=%s 代码=%s", ip, fails, exc.code)


@router.post("/refresh", summary="刷新访问令牌")
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return JSONResponse(
            status_code=401,
            content=error_payload("AUTH_REFRESH_MISSING", "缺少刷新凭证，请重新登录"),
        )
    try:
        result = await service.refresh_session(
            db,
            refresh_token=token,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AppError as exc:
        error_response = JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )
        _clear_refresh_cookie(error_response)
        return error_response
    refresh_token = result.pop("refresh_token")
    _set_refresh_cookie(response, refresh_token)
    return success(result)


@router.post("/logout", summary="退出登录")
async def logout(
    response: Response,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.logout(db, ctx.require_session())
    _clear_refresh_cookie(response)
    return success(None, "已退出登录")


@router.get("/me", summary="当前用户信息")
async def me(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    menus = await system_service.current_user_menu(db, ctx)
    return success(
        {
            "user": user_out(ctx.user),
            "permissions": sorted(ctx.permissions),
            "is_superuser": ctx.is_superuser,
            "is_admin": ctx.is_admin,
            "menus": menus,
            # API Key 身份没有会话，这里返回 null 让前端能区分两种身份
            "session": session_out(ctx.session, current=True) if ctx.session is not None else None,
            "via_api_key": ctx.session is None,
        }
    )


@router.post("/heartbeat", summary="在线心跳")
async def heartbeat(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await service.touch_session(db, ctx.require_session())
    return success({"server_time": iso(utcnow())})


@router.post("/change-password", summary="修改当前用户密码")
async def change_password(
    data: ChangePasswordRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await service.change_password(db, user=ctx.user, session=ctx.require_session(), data=data)
    return success(result, "密码修改成功")


@router.get("/sessions", summary="我的登录会话")
async def my_sessions(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ctx.require_session()
    return success(await system_service.list_user_sessions(db, ctx))


@router.delete("/sessions/{session_id}", summary="撤销我的指定会话")
async def revoke_my_session(
    session_id: str,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await system_service.revoke_user_session(db, ctx, session_id)
    return success(None, "会话已撤销")


@router.delete("/sessions", summary="撤销我的其他会话")
async def revoke_other_sessions(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    count = await system_service.revoke_other_sessions(db, ctx)
    return success({"revoked": count})


@router.put("/profile", summary="更新个人资料")
async def update_profile(
    data: ProfileUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await system_service.update_profile(db, ctx, data), "个人资料已更新")


@router.get("/avatar", summary="我的头像")
async def get_avatar(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not ctx.user.avatar_file_id:
        raise AppError("AUTH_AVATAR_NOT_FOUND", "尚未设置头像", 404)
    item = await db.get(FileObject, ctx.user.avatar_file_id)
    if item is None or not content_service.is_safe_image(item):
        raise AppError("AUTH_AVATAR_NOT_FOUND", "头像文件不存在", 404)
    return FileResponse(
        content_service.file_path(item),
        media_type=item.content_type,
        headers={
            "Content-Disposition": 'inline; filename="avatar"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/avatar", summary="上传我的头像")
async def upload_avatar(
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    uploaded = await content_service.upload_file(
        db, ctx, filename=file.filename, content_type=file.content_type, stream=file, image_only=True
    )
    ctx.user.avatar_file_id = int(uploaded["id"])
    await db.commit()
    await db.refresh(ctx.user)
    return success(user_out(ctx.user), "头像已更新")
