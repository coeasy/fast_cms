"""鉴权上下文：把一次请求的身份、会话、权限、数据范围收在一个对象里。

放在 ``app/kernel`` 而不是 ``app/core``：它要查 ``User`` / ``Role`` / ``Permission`` /
``UserSession`` / ``ApiKey``，是「有状态的底座」。此前它留在 core，导致
``kernel/db.py``、``kernel/scope.py``、``kernel/files.py`` 三个同层模块反过来 import 它，
方向成了双向，等于没有方向。客户端地址解析这类纯函数已单独下沉到 ``app/core/net.py``。

``app/core/authz.py`` 保留 re-export shim，调用点无需改动；下一轮删除。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any, cast

from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bootstrap.permissions import ADMIN_PERMISSION
from app.core.config import api_path, settings
from app.core.errors import AppError
from app.core.net import client_ip
from app.core.security import decode_access_token
from app.core.serializers import iso
from app.db.base import utcnow
from app.db.session import get_db
from app.kernel.models import Dept, Permission, Role, User, UserSession

bearer_scheme = HTTPBearer(auto_error=False)

# 匿名访问的占位用户：公开接口（如前台读取已发布公告）在无 token 时用它构造上下文。
# 只带属性访问所需的最小字段，`id=None` 使一切按 user_id 关联的查询自然匹配不到行，
# 权限判断恒为 False，绝不可能绕过鉴权。
@dataclass(frozen=True)
class _AnonymousUser:
    id: int | None = None
    username: str = "anonymous"
    status: str = "active"
    is_superuser: bool = False
    is_banned: bool = False
    ban_reason: str | None = None
    banned_until: Any = None


ANONYMOUS_USER = _AnonymousUser()

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
    # 已废弃的权限：{code: deprecated_at}。宽限期内这些码**仍留在 permissions 里**
    # （菜单、按钮、角色分配校验照常工作），另外记一份时间戳供 require_permission
    # 回 Deprecation/Sunset 头；过期后从 permissions 摘除并拒绝。
    deprecated: dict[str, datetime] = field(default_factory=dict)
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
            raise AppError("AUTH_SESSION_REQUIRED", status_code=400)
        return self.session


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("AUTH_UNAUTHORIZED", status_code=401)
    if credentials.credentials.startswith(API_KEY_MARKER):
        return await _context_from_api_key(db, credentials.credentials, request)
    return await _context_from_token(db, credentials.credentials, request)


async def optional_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """可选鉴权：带 token 走完整登录校验，无 token 以匿名上下文放行。

    供公开但可感知登录态的接口使用（前台读取已发布公告、公开配置等）。
    匿名时 ``ctx.user.id is None``、``ctx.permissions`` 为空，所有
    ``ctx.has(...)`` 恒为 False，不会泄露任何权限。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        # 匿名哨兵只实现 User 的只读字段（id=None 等），不写库、不进 session；
        # 用 cast 让类型检查通过，运行期行为由 `ctx.user.id is None` 判定。
        return AuthContext(user=cast(User, ANONYMOUS_USER), session=None, permissions=set())
    try:
        if credentials.credentials.startswith(API_KEY_MARKER):
            return await _context_from_api_key(db, credentials.credentials, request)
        return await _context_from_token(db, credentials.credentials, request)
    except AppError as exc:
        # 公开接口的鉴权是「尽力而为」：浏览器里残留的过期 token、被封账号的
        # 旧凭证，都不该让匿名可读的公告页/公开配置直接 500 或 401——那会把
        # 一个纯公开页面变成必须登录才能看。降级为匿名，个性化信息自然消失。
        if exc.status_code >= 500:
            raise
        return AuthContext(user=cast(User, ANONYMOUS_USER), session=None, permissions=set())


async def _context_from_token(db: AsyncSession, raw: str, request: Request) -> AuthContext:
    try:
        payload = decode_access_token(raw)
    except Exception as exc:
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
        raise AppError("AUTH_PASSWORD_CHANGE_REQUIRED", status_code=403)

    permissions: set[str] = set()
    deprecated: dict[str, datetime] = {}
    for role in user.roles:
        if role.status != "active":
            continue
        for item in role.permissions:
            if item.deprecated_at is None:
                permissions.add(item.code)
                continue
            deprecated[item.code] = item.deprecated_at
            # 宽限期内仍然算「持有」：菜单过滤、data-perm 按钮、角色分配时的
            # 「不能授予自己没有的权限」都读 permissions。若这里直接摘掉，废弃
            # 权限在 t=0 就等于被回收，宽限期只剩 API 层看得见，Web 界面会静默
            # 少掉入口。过期后再摘，此时 require_permission 抛 SUNSET。
            if _within_grace(item.deprecated_at):
                permissions.add(item.code)

    request.state.user_id = user.id
    request.state.username = user.username
    return AuthContext(user=user, session=session, permissions=permissions, deprecated=deprecated)


async def _context_from_api_key(db: AsyncSession, raw: str, request: Request) -> AuthContext:
    """按 API Key 构造鉴权上下文。

    Key 本身不是用户，所以创建 Key 时会派生一个服务账号，数据范围、审计署名、
    封禁都挂在这个账号上。**Key 声明的 scopes 就是它的权限集**：服务账号按设计
    不挂角色，再和角色取交集只会得到空集（那就没有任何 Key 能用了）。
    防止越权改在创建端做——创建人只能授予自己持有的权限码。
    """
    from app.kernel.api_key import resolve_api_key

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
    # Key 的 scopes 是字符串，要判断其中哪些权限已废弃，得查一次权限表。
    # 与浏览器会话同口径：宽限期内仍算持有，过期才摘掉。
    deprecated: dict[str, datetime] = {}
    concrete = declared - {"*"}
    if concrete:
        rows = (
            await db.execute(
                select(Permission.code, Permission.deprecated_at).where(
                    Permission.code.in_(concrete),
                    Permission.deprecated_at.is_not(None),
                )
            )
        ).all()
        for code, dep_at in rows:
            if dep_at is not None:
                deprecated[str(code)] = dep_at
    expired = {code for code, at in deprecated.items() if not _within_grace(at)}
    request.state.user_id = user.id
    request.state.username = user.username
    return AuthContext(
        user=user,
        session=None,
        permissions=declared - expired,
        deprecated=deprecated,
        all_permissions="*" in declared,
    )


def _as_utc(value: datetime) -> datetime:
    """把可能缺时区的 datetime 归一成 UTC-aware（DB 读出的时间按 UTC 解释）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sunset_at(deprecated_at: datetime) -> datetime:
    """废弃权限的下线时刻 = 标记时间 + 宽限期。"""
    return _as_utc(deprecated_at) + timedelta(days=settings.permission_deprecation_grace_days)


def _within_grace(deprecated_at: datetime) -> bool:
    return utcnow() < _sunset_at(deprecated_at)


def require_permission(code: str) -> Callable[..., Awaitable[AuthContext]]:
    async def dependency(
        response: Response,
        ctx: AuthContext = Depends(get_current_user),
    ) -> AuthContext:
        # 先判废弃：宽限期内权限仍在 permissions 里（菜单/按钮照常），所以不能
        # 让 has() 先返回——那样就永远不会带 Deprecation/Sunset 头，客户端也就
        # 收不到迁移信号。
        #
        # 超管 / API Key 的 ``*`` 不受废弃影响：它们不是「持有某个具体权限码」，
        # 而是绕过权限判断。若也让它们走废弃分支，一个过期权限码会把超管挡在门外。
        if not ctx.is_superuser and not ctx.all_permissions:
            deprecated_at = ctx.deprecated.get(code)
            if deprecated_at is not None:
                sunset = _sunset_at(deprecated_at)
                if utcnow() >= sunset:
                    raise AppError("AUTH_PERMISSION_SUNSET", status_code=403, params={"code": code})
                response.headers["Deprecation"] = "true"
                response.headers["Sunset"] = format_datetime(sunset, usegmt=True)
                return ctx
        if ctx.has(code):
            return ctx
        raise AppError("AUTH_FORBIDDEN", f"缺少权限：{code}", 403)

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
            # 前缀匹配取代 "%/<dept_id>/%"：用用户部门的物化路径前缀找全部后代，
            # 避免前置通配绕过索引（P1-7）。ctx.user.dept 已由调用方 selectinload。
            user_dept = ctx.user.dept
            dept_path = (user_dept.ancestors if user_dept else "") + f"{ctx.user.dept_id}/"
            descendants = await db.execute(
                select(Dept.id).where(
                    or_(
                        Dept.id == ctx.user.dept_id,
                        Dept.ancestors.like(f"{dept_path}%"),
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
