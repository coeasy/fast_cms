from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bootstrap.permissions import ADMIN_PERMISSION
from app.core.config import api_path, settings
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.core.serializers import iso
from app.db.base import utcnow
from app.db.session import get_db
from app.modules.system.models import Dept, Role, User, UserSession

bearer_scheme = HTTPBearer(auto_error=False)

PASSWORD_CHANGE_WHITELIST = (
    "/auth/me",
    "/auth/logout",
    "/auth/change-password",
    "/auth/heartbeat",
    "/auth/profile",
    "/auth/refresh",
)

# API Key 的字面前缀。JWT 固定以 base64 的 "eyJ" 开头，两者不可能混淆，
# 所以可以只看前缀分流，不需要先试解析再回退。
API_KEY_MARKER = "fck_"


@dataclass
class AuthContext:
    user: User
    # 会话为 None 表示这是 API Key 身份：没有浏览器会话可吊销，
    # 处置手段是停用 Key 或封禁其服务账号
    session: UserSession | None
    permissions: set[str] = field(default_factory=set)
    # API Key 可以用 ``*`` 声明全部权限。用显式标志而不是把星号塞进 permissions，
    # 否则所有 ``code in permissions`` 的判断都会被一个星号搅乱。
    all_permissions: bool = False

    @property
    def is_superuser(self) -> bool:
        return self.user.is_superuser

    @property
    def is_admin(self) -> bool:
        return self.has(ADMIN_PERMISSION)

    def has(self, code: str) -> bool:
        return self.all_permissions or self.is_superuser or code in self.permissions

    def require_session(self) -> UserSession:
        """取当前会话；API Key 身份没有会话，直接拒绝。

        退出登录、心跳、改密、会话列表都建立在会话之上。返回具体类型而不是让
        调用方自己判空，mypy 才能把 ``UserSession | None`` 收窄成 ``UserSession``，
        也不给「拿 Key 去吊销别人会话」留路。
        """
        if self.session is None:
            raise AppError("AUTH_SESSION_REQUIRED", "当前身份没有登录会话", 400)
        return self.session


def client_ip_from_scope(scope: Any) -> str | None:
    """从 ASGI scope 里解析客户端 IP。

    中间件改纯 ASGI 后拿不到 ``Request`` 对象，抽一个 scope 版本共用同一套判定：
    uvicorn 只有在直连来源属于 FORWARDED_ALLOW_IPS 时才用 X-Forwarded-For 改写
    scope["client"]，所以默认只信任 peer；显式开启 TRUST_PROXY 后才采用原始 XFF，
    否则客户端可伪造该头绕过登录限流并污染审计日志。
    """
    if settings.trust_proxy:
        for key, value in scope.get("headers") or ():
            if key == b"x-forwarded-for":
                first = value.decode("latin-1").split(",")[0].strip()
                if first:
                    return first[:64]
                break
    client = scope.get("client") or ()
    peer = client[0] if client else None
    return peer[:64] if peer else None


def client_ip(request: Request) -> str | None:
    return client_ip_from_scope(request.scope)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("AUTH_UNAUTHORIZED", "请先登录", 401)
    if credentials.credentials.startswith(API_KEY_MARKER):
        return await _context_from_api_key(db, credentials.credentials, request)
    return await _context_from_token(db, credentials.credentials, request)


async def _context_from_token(db: AsyncSession, raw: str, request: Request) -> AuthContext:
    try:
        payload = decode_access_token(raw)
    except Exception as exc:  # noqa: BLE001
        raise AppError("AUTH_TOKEN_INVALID", "登录凭证无效或已过期", 401) from exc

    try:
        user_id = int(payload.get("sub", "0"))
    except ValueError as exc:
        raise AppError("AUTH_TOKEN_INVALID", "登录凭证无效", 401) from exc

    statement = (
        select(User)
        .options(
            selectinload(User.dept),
            selectinload(User.roles).selectinload(Role.permissions),
            selectinload(User.roles).selectinload(Role.departments),
        )
        .where(User.id == user_id)
    )
    user = (await db.execute(statement)).scalar_one_or_none()
    if user is None or user.status != "active":
        raise AppError("AUTH_USER_DISABLED", "账号不存在或已被禁用", 401)

    # 封禁必须排在会话与 token_version 校验之前：封禁会撤销全部会话并抬高版本号，
    # 若先校验那两个，被封用户只会看到「会话已失效，请重新登录」，
    # 被引到登录页才知道原因。先判封禁才能把原因和解封时间直接回给前端。
    if user.is_banned:
        raise AppError(
            "AUTH_ACCOUNT_BANNED",
            "账号已被封禁",
            403,
            {
                "reason": user.ban_reason,
                "until": iso(user.banned_until) if user.banned_until else None,
                "permanent": user.banned_until is None,
            },
        )

    sid = str(payload.get("sid", ""))
    session = await db.get(UserSession, sid)
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        raise AppError("AUTH_SESSION_EXPIRED", "会话已失效，请重新登录", 401)
    if session.user_id != user.id:
        raise AppError("AUTH_TOKEN_INVALID", "登录凭证与会话不匹配", 401)

    if int(payload.get("ver", -1)) != user.token_version:
        raise AppError("AUTH_SESSION_EXPIRED", "会话已失效，请重新登录", 401)

    if user.must_change_password and request.url.path not in {api_path(item) for item in PASSWORD_CHANGE_WHITELIST}:
        raise AppError("AUTH_PASSWORD_CHANGE_REQUIRED", "首次登录必须修改密码", 403)

    permissions: set[str] = set()
    for role in user.roles:
        if role.status != "active":
            continue
        permissions.update(item.code for item in role.permissions if item.deprecated_at is None)

    request.state.user_id = user.id
    request.state.username = user.username
    return AuthContext(user=user, session=session, permissions=permissions)


async def _context_from_api_key(db: AsyncSession, raw: str, request: Request) -> AuthContext:
    """按 API Key 构造鉴权上下文。

    Key 本身不是用户，所以创建 Key 时会派生一个服务账号，数据范围、审计署名、
    封禁都挂在这个账号上。**Key 声明的 scopes 就是它的权限集**：服务账号按设计
    不挂角色，再和角色取交集只会得到空集（那就没有任何 Key 能用了）。
    防止越权改在创建端做——创建人只能授予自己持有的权限码。
    """
    from app.modules.security.service import resolve_api_key

    key = await resolve_api_key(db, raw, client_ip(request))
    if key is None:
        raise AppError("AUTH_TOKEN_INVALID", "API Key 无效、已停用或已过期", 401)
    if key.user_id is None:
        raise AppError("AUTH_TOKEN_INVALID", "API Key 未绑定服务账号，请重新创建", 401)

    statement = (
        select(User)
        .options(
            selectinload(User.dept),
            selectinload(User.roles).selectinload(Role.permissions),
            selectinload(User.roles).selectinload(Role.departments),
        )
        .where(User.id == key.user_id)
    )
    user = (await db.execute(statement)).scalar_one_or_none()
    if user is None or user.status != "active":
        raise AppError("AUTH_USER_DISABLED", "服务账号不存在或已被禁用", 401)
    if user.is_banned:
        raise AppError(
            "AUTH_ACCOUNT_BANNED",
            "服务账号已被封禁",
            403,
            {
                "reason": user.ban_reason,
                "until": iso(user.banned_until) if user.banned_until else None,
                "permanent": user.banned_until is None,
            },
        )

    declared = {part.strip() for part in key.scopes.split(",") if part.strip()}
    request.state.user_id = user.id
    request.state.username = user.username
    return AuthContext(
        user=user,
        session=None,
        permissions=declared,
        all_permissions="*" in declared,
    )


def require_permission(code: str):
    async def dependency(ctx: AuthContext = Depends(get_current_user)) -> AuthContext:
        if not ctx.has(code):
            raise AppError("AUTH_FORBIDDEN", f"缺少权限：{code}", 403)
        return ctx

    return dependency


def require_admin(ctx: AuthContext = Depends(get_current_user)) -> AuthContext:
    if not ctx.is_admin:
        raise AppError("AUTH_FORBIDDEN", "仅管理员可访问管理后台", 403)
    return ctx


@dataclass
class DataScope:
    all_access: bool = False
    include_self: bool = False
    dept_ids: set[int] = field(default_factory=set)


async def get_data_scope(db: AsyncSession, ctx: AuthContext) -> DataScope:
    # ``*`` 的 API Key 声明了全部权限，数据范围也必须跟着放开，
    # 否则它会拿到一个空范围，列表接口静默返回空——比直接报错更难排查
    if ctx.is_superuser or ctx.all_permissions:
        return DataScope(all_access=True)
    scope = DataScope()
    active_roles = [role for role in ctx.user.roles if role.status == "active"]
    if not active_roles:
        return scope

    for role in active_roles:
        if role.data_scope == "all":
            return DataScope(all_access=True)
        if role.data_scope == "self":
            scope.include_self = True
        elif role.data_scope == "dept" and ctx.user.dept_id:
            scope.dept_ids.add(ctx.user.dept_id)
        elif role.data_scope == "dept_and_below" and ctx.user.dept_id:
            descendants = await db.execute(
                select(Dept.id).where(
                    or_(
                        Dept.id == ctx.user.dept_id,
                        Dept.ancestors.like(f"%/{ctx.user.dept_id}/%"),
                    )
                )
            )
            scope.dept_ids.update(int(item) for item in descendants.scalars().all())
        elif role.data_scope == "custom":
            scope.dept_ids.update(dept.id for dept in role.departments)
    return scope


def apply_user_scope(statement: Select, ctx: AuthContext, scope: DataScope) -> Select:
    if scope.all_access:
        return statement
    conditions = []
    if scope.include_self:
        conditions.append(User.id == ctx.user.id)
    if scope.dept_ids:
        conditions.append(User.dept_id.in_(scope.dept_ids))
    if not conditions:
        return statement.where(User.id == -1)
    return statement.where(or_(*conditions))


def user_in_scope(ctx: AuthContext, scope: DataScope, user: User) -> bool:
    if ctx.is_superuser or scope.all_access:
        return True
    if scope.include_self and user.id == ctx.user.id:
        return True
    return bool(user.dept_id and user.dept_id in scope.dept_ids)


async def scope_user_ids(db: AsyncSession, ctx: AuthContext) -> Select | None:
    """范围内用户的 id 子查询；返回 ``None`` 表示不需要过滤。"""
    if ctx.is_superuser:
        return None
    scope = await get_data_scope(db, ctx)
    if scope.all_access:
        return None
    return apply_user_scope(select(User.id), ctx, scope)
