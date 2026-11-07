from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, client_ip, get_current_user
from app.core.cache import get_cache
from app.core.config import settings
from app.core.csrf import ensure_same_origin
from app.core.envelope import BINARY_RESPONSES, Envelope
from app.core.errors import AppError, error_payload, get_request_id, success
from app.core.serializers import iso
from app.core.upload import is_safe_image_content_type
from app.db.base import utcnow
from app.db.session import get_db
from app.kernel import files as file_service
from app.kernel.db import commit
from app.kernel.models import FileObject
from app.kernel.schemas import SessionOut, UserOut
from app.kernel.serializers import session_out, user_out
from app.modules.sys.auth import service
from app.modules.sys.auth.schemas import (
    ChangePasswordRequest,
    HeartbeatOut,
    LoginOut,
    LoginRequest,
    MeOut,
    RevokedOut,
    TokenOut,
)
from app.modules.sys.security import service as security_service
from app.modules.sys.system import service as system_service
from app.modules.sys.system.schemas import ProfileUpdate

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


@router.post("/login", summary="登录", response_model=Envelope[LoginOut])
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
        raise AppError("AUTH_RATE_LIMITED", status_code=429)
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
    """累计失败次数，达到阈值时自动生成一条临时封禁网段。

    P0-7：异常按「已存在 / 真实失败」分类记录并携带 request_id，
    不再静默吞掉；封禁成功后重置计数，避免后续失败反复尝试重复封禁。
    """
    if not ip:
        return
    cache = get_cache()
    window = max(60, settings.auth_ip_ban_minutes * 60)
    fails = await cache.incr(_fail_key(ip), window)
    if fails < settings.auth_max_failed:
        return
    reason = f"登录失败 {fails} 次后自动封禁"
    request_id = get_request_id()
    try:
        await security_service.block_ip_now(
            db,
            None,
            ip=ip,
            scope="api",
            reason=reason,
            minutes=settings.auth_ip_ban_minutes,
        )
    except AppError as ban_exc:
        if ban_exc.code == "SECURITY_IP_RULE_DUPLICATED":
            # 已有同名规则说明封禁仍在有效期内，直接复用即可，属于预期路径
            logger.info(
                "自动封禁已存在 IP=%s 失败次数=%s 登录码=%s 封禁码=%s request_id=%s",
                ip, fails, exc.code, ban_exc.code, request_id,
            )
        else:
            # 真实失败（数据库异常等）：必须告警并保留 request_id 供排障
            logger.error(
                "自动封禁失败 IP=%s 失败次数=%s 登录码=%s 封禁码=%s request_id=%s",
                ip, fails, exc.code, ban_exc.code, request_id,
            )
        return
    # 封禁成功：重置计数，让下一个窗口重新累计
    await cache.delete(_fail_key(ip))
    logger.warning(
        "自动封禁登录失败来源 IP=%s 失败次数=%s 代码=%s request_id=%s",
        ip, fails, exc.code, request_id,
    )


@router.post("/refresh", summary="刷新访问令牌", response_model=Envelope[LoginOut])
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    # Cookie 认证接口：SameSite=Lax 之外再叠加同源校验，阻断跨源驱动（P0-1）
    ensure_same_origin(request)
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
    # 并发刷新分支只补发 access token、不轮换 refresh token，此时不应改写 cookie：
    # 浏览器里已有的那份才是当前有效的，覆盖它会制造新的废票。
    refresh_token = result.pop("refresh_token", None)
    if refresh_token:
        _set_refresh_cookie(response, refresh_token)
    return success(result)


@router.post("/logout", summary="退出登录", response_model=Envelope[None])
async def logout(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ensure_same_origin(request)
    await service.logout(db, ctx.require_session())
    _clear_refresh_cookie(response)
    return success(None, "已退出登录")


@router.get("/me", summary="当前用户信息", response_model=Envelope[MeOut])
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


@router.post("/heartbeat", summary="在线心跳", response_model=Envelope[HeartbeatOut])
async def heartbeat(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await service.touch_session(db, ctx.require_session())
    return success({"server_time": iso(utcnow())})


@router.post("/change-password", summary="修改当前用户密码", response_model=Envelope[TokenOut])
async def change_password(
    data: ChangePasswordRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await service.change_password(db, user=ctx.user, session=ctx.require_session(), data=data)
    return success(result, "密码修改成功")


@router.get("/sessions", summary="我的登录会话", response_model=Envelope[list[SessionOut]])
async def my_sessions(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ctx.require_session()
    return success(await system_service.list_user_sessions(db, ctx))


@router.delete("/sessions/{session_id}", summary="撤销我的指定会话", response_model=Envelope[None])
async def revoke_my_session(
    session_id: str,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await system_service.revoke_user_session(db, ctx, session_id)
    return success(None, "会话已撤销")


@router.delete("/sessions", summary="撤销我的其他会话", response_model=Envelope[RevokedOut])
async def revoke_other_sessions(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    count = await system_service.revoke_other_sessions(db, ctx)
    return success({"revoked": count})


@router.put("/profile", summary="更新个人资料", response_model=Envelope[UserOut])
async def update_profile(
    data: ProfileUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await system_service.update_profile(db, ctx, data), "个人资料已更新")


@router.get(
    "/avatar",
    summary="我的头像",
    response_class=FileResponse,
    responses=BINARY_RESPONSES,
)
async def get_avatar(ctx: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not ctx.user.avatar_file_id:
        raise AppError("AUTH_AVATAR_NOT_FOUND", "尚未设置头像", 404)
    item = await db.get(FileObject, ctx.user.avatar_file_id)
    if item is None or not is_safe_image_content_type(item.content_type):
        raise AppError("AUTH_AVATAR_NOT_FOUND", "头像文件不存在", 404)
    return FileResponse(
        file_service.file_path(item),
        media_type=item.content_type,
        headers={
            "Content-Disposition": 'inline; filename="avatar"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/avatar", summary="上传我的头像", response_model=Envelope[UserOut])
async def upload_avatar(
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    uploaded = await file_service.upload_file(
        db, ctx, filename=file.filename, content_type=file.content_type, stream=file, image_only=True
    )
    ctx.user.avatar_file_id = int(uploaded["id"])
    await commit(db)
    await db.refresh(ctx.user)
    return success(user_out(ctx.user), "头像已更新")
