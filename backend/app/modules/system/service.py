from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import ipgeo
from app.core.authz import AuthContext, DataScope, apply_user_scope, get_data_scope, user_in_scope
from app.core.config import api_path
from app.core.csvio import optional_int, optional_text, read_rows, split_ids
from app.core.errors import AppError, get_request_id
from app.core.pagination import PageParams, paginate
from app.core.request_context import current_context
from app.core.security import hash_password, validate_password_strength
from app.db.base import rowcount, utcnow
from app.modules.audit.models import LoginLog, OperationLog
from app.modules.config.service import password_min_length
from app.modules.content.models import FileObject
from app.modules.content.service import is_safe_image
from app.modules.system.models import (
    Dept,
    Menu,
    Permission,
    Role,
    User,
    UserSession,
    role_depts,
    user_roles,
)
from app.modules.system.schemas import (
    AssignPermissionsRequest,
    AssignRolesRequest,
    DataScopeUpdate,
    DeptCreate,
    DeptUpdate,
    MenuCreate,
    MenuUpdate,
    ProfileUpdate,
    ResetPasswordRequest,
    RoleCreate,
    RoleUpdate,
    UserCreate,
    UserUpdate,
)
from app.modules.system.serializers import (
    dept_out,
    menu_out,
    permission_out,
    role_out,
    session_out,
    user_out,
)

USERNAME_ADMIN = "admin"
# 最后登录 IP 属于登录审计数据，只有能查看登录日志的角色才能看到别人的
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


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError("DATA_CONFLICT", "数据重复或违反约束，请检查后重试", 409) from exc


async def _log_operation(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    module: str,
    action: str,
    detail: str | None = None,
    method: str = "POST",
    path: str = "",
) -> None:
    http = current_context()
    db.add(
        OperationLog(
            user_id=ctx.user.id,
            username=ctx.user.username,
            module=module,
            action=action,
            method=method,
            path=api_path(path)[:255] if path else "",
            status_code=200,
            success=True,
            duration_ms=0,
            ip=http.ip,
            user_agent=http.user_agent,
            detail=detail,
            request_id=get_request_id() or None,
        )
    )


async def _user_scope(db: AsyncSession, ctx: AuthContext) -> DataScope:
    return await get_data_scope(db, ctx)


async def _can_access_user(db: AsyncSession, ctx: AuthContext, user: User) -> bool:
    return user_in_scope(ctx, await _user_scope(db, ctx), user)


async def _ensure_user_access(db: AsyncSession, ctx: AuthContext, user: User | None) -> User:
    if user is None:
        raise AppError("SYSTEM_USER_NOT_FOUND", "用户不存在", 404)
    if not await _can_access_user(db, ctx, user):
        raise AppError("SYSTEM_USER_FORBIDDEN", "无权操作该用户", 403)
    return user


# 以下是对外公开的复用入口：安全模块（封禁/解封）、其他模块需要同一套
# 「数据范围校验 + 会话回收 + 操作日志」语义时，直接调这些而不是各自实现一遍。
async def ensure_user_access(db: AsyncSession, ctx: AuthContext, user: User | None) -> User:
    """校验目标用户是否在当前角色的数据范围内，越权直接 403。"""
    return await _ensure_user_access(db, ctx, user)


async def revoke_user_sessions(db: AsyncSession, user_id: int, *, keep_id: str | None = None) -> None:
    """撤销用户全部未失效会话；``keep_id`` 用于保留当前登录（如自助改密）。"""
    return await _revoke_sessions(db, user_id, keep_id=keep_id)


async def log_operation(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    module: str,
    action: str,
    detail: str | None = None,
    method: str = "POST",
    path: str = "",
) -> None:
    """写一条系统操作日志；调用方负责在同一事务里提交。"""
    return await _log_operation(
        db, ctx, module=module, action=action, detail=detail, method=method, path=path
    )


def _ensure_can_manage_superuser(ctx: AuthContext, target_superuser: bool) -> None:
    if target_superuser and not ctx.is_superuser:
        raise AppError("SYSTEM_USER_FORBIDDEN", "仅超级管理员可操作超级管理员账号", 403)


async def _ensure_new_user_scope(db: AsyncSession, ctx: AuthContext, dept_id: int | None) -> None:
    if ctx.is_superuser:
        return
    scope = await _user_scope(db, ctx)
    if scope.all_access:
        return
    if not dept_id or dept_id not in scope.dept_ids:
        raise AppError("SYSTEM_USER_DEPT_FORBIDDEN", "目标部门不在你的数据范围内", 403)


async def _last_superuser_guard(db: AsyncSession, user_id: int) -> None:
    total = int(
        (
            await db.execute(
                select(func.count(User.id)).where(User.is_superuser.is_(True), User.id != user_id)
            )
        ).scalar()
        or 0
    )
    if total == 0:
        raise AppError("SYSTEM_LAST_SUPERUSER", "系统必须保留至少一个超级管理员", 409)


async def _load_roles_for_assignment(
    db: AsyncSession, ctx: AuthContext, role_ids: list[int]
) -> list[Role]:
    if not role_ids:
        return []
    roles = (
        (
            await db.execute(
                select(Role)
                .options(selectinload(Role.permissions))
                .where(Role.id.in_(set(role_ids)))
            )
        )
        .scalars()
        .all()
    )
    if len(roles) != len(set(role_ids)):
        raise AppError("SYSTEM_ROLE_NOT_FOUND", "部分角色不存在", 404)
    if ctx.is_superuser:
        return list(roles)
    scope = await _user_scope(db, ctx)
    allowed_scope = {"self", "dept"} if not scope.all_access else {"self", "dept", "dept_and_below", "custom", "all"}
    for role in roles:
        if role.status != "active":
            raise AppError("SYSTEM_ROLE_DISABLED", f"角色「{role.name}」已停用", 409)
        if role.data_scope not in allowed_scope:
            raise AppError("SYSTEM_ROLE_ESCALATION", "不能分配数据范围超过你的角色", 403)
        codes = {item.code for item in role.permissions if item.deprecated_at is None}
        if not codes.issubset(ctx.permissions):
            raise AppError("SYSTEM_ROLE_ESCALATION", f"不能分配权限超过你的角色「{role.name}」", 403)
    return list(roles)


async def _set_user_roles(db: AsyncSession, user: User, roles: list[Role]) -> None:
    user.roles = roles


async def _revoke_sessions(db: AsyncSession, user_id: int, *, keep_id: str | None = None) -> None:
    statement = update(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
    )
    if keep_id:
        statement = statement.where(UserSession.id != keep_id)
    await db.execute(statement.values(revoked_at=utcnow()))


async def list_users(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
    *,
    keyword: str | None = None,
    dept_id: int | None = None,
    status: str | None = None,
    role_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    scope = await _user_scope(db, ctx)
    statement = select(User).options(selectinload(User.dept), selectinload(User.roles))
    statement = apply_user_scope(statement, ctx, scope)
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(
            or_(
                User.username_normalized.like(like),
                func.lower(User.nickname).like(like),
                User.phone.like(like),
                func.lower(User.email).like(like),
            )
        )
    if dept_id:
        subtree = (
            (
                await db.execute(
                    select(Dept.id).where(
                        or_(Dept.id == dept_id, Dept.ancestors.like(f"%/{dept_id}/%"))
                    )
                )
            )
            .scalars()
            .all()
        )
        if not scope.all_access:
            subtree = [item for item in subtree if item in scope.dept_ids]
        statement = statement.where(User.dept_id.in_(subtree))
    if status:
        statement = statement.where(User.status == status)
    if role_id:
        statement = statement.where(User.id.in_(select(user_roles.c.user_id).where(user_roles.c.role_id == role_id)))
    statement = statement.order_by(User.id.desc())
    users, total = await paginate(db, statement, params)
    locations = await ipgeo.locate_many(item.last_login_ip for item in users)
    return [_user_payload(ctx, item, locations) for item in users], total


async def get_user(db: AsyncSession, ctx: AuthContext, user_id: int) -> dict[str, Any]:
    user = (
        (
            await db.execute(
                select(User)
                .options(selectinload(User.dept), selectinload(User.roles))
                .where(User.id == user_id)
            )
        )
        .scalars()
        .first()
    )
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    locations = await ipgeo.locate_many([user.last_login_ip])
    return _user_payload(ctx, user, locations)


async def create_user(
    db: AsyncSession, ctx: AuthContext, data: UserCreate, *, commit: bool = True
) -> dict[str, Any]:
    """创建用户。

    ``commit=False`` 供导入预检使用：走完整的校验与落库路径但不提交，
    调用方在批次结束后统一 ``rollback``。这样预检能发现真实写入时才会
    暴露的问题（部门不存在、角色越权、用户名撞车），而不是只做一遍
    和第二套逻辑对不上的「纸上校验」。
    """
    username = data.username.strip()
    normalized = _normalize_username(username)
    exists = (await db.execute(select(User.id).where(User.username_normalized == normalized))).scalar_one_or_none()
    if exists:
        raise AppError("SYSTEM_USERNAME_EXISTS", "用户名已存在", 409)
    valid, message = validate_password_strength(
        data.password, username, min_length=await password_min_length(db)
    )
    if not valid:
        raise AppError("SYSTEM_WEAK_PASSWORD", message, 422)
    if data.is_superuser and not ctx.is_superuser:
        raise AppError("SYSTEM_USER_FORBIDDEN", "仅超级管理员可创建超级管理员", 403)
    await _ensure_new_user_scope(db, ctx, data.dept_id)
    if data.dept_id:
        dept = await db.get(Dept, data.dept_id)
        if dept is None:
            raise AppError("SYSTEM_DEPT_NOT_FOUND", "部门不存在", 404)
    roles = await _load_roles_for_assignment(db, ctx, data.role_ids)
    user = User(
        username=username,
        username_normalized=normalized,
        password_hash=hash_password(data.password),
        nickname=data.nickname.strip(),
        email=data.email,
        phone=data.phone,
        dept_id=data.dept_id,
        status=data.status,
        is_superuser=data.is_superuser,
        must_change_password=data.must_change_password or data.is_superuser,
        remark=data.remark,
    )
    user.roles = roles
    db.add(user)
    await _log_operation(db, ctx, module="system", action="user.create", detail=f"username={username}", path="/system/users")
    if commit:
        await _commit(db)
    else:
        # 预检模式：必须 flush 才能拿到自增主键、触发唯一约束，
        # 否则后面的重查会因为 user.id 还是 None 而什么都查不到
        await db.flush()
    created = (
        (
            await db.execute(
                select(User)
                .options(selectinload(User.dept), selectinload(User.roles))
                .where(User.id == user.id)
            )
        )
        .scalars()
        .first()
    )
    assert created is not None
    locations = await ipgeo.locate_many([created.last_login_ip])
    return _user_payload(ctx, created, locations)


async def update_user(db: AsyncSession, ctx: AuthContext, user_id: int, data: UserUpdate) -> dict[str, Any]:
    user = (
        (await db.execute(select(User).options(selectinload(User.roles), selectinload(User.dept)).where(User.id == user_id)))
        .scalars()
        .first()
    )
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    payload = data.model_dump(exclude_unset=True)
    role_ids = payload.pop("role_ids", None)
    if "dept_id" in payload and payload["dept_id"] != user.dept_id:
        dept_id = payload["dept_id"]
        await _ensure_new_user_scope(db, ctx, dept_id)
        if dept_id is not None:
            dept = await db.get(Dept, dept_id)
            if dept is None:
                raise AppError("SYSTEM_DEPT_NOT_FOUND", "部门不存在", 404)
    if payload.get("is_superuser") is not None:
        if not ctx.is_superuser:
            raise AppError("SYSTEM_USER_FORBIDDEN", "仅超级管理员可调整超级管理员标记", 403)
        if user.id == ctx.user.id and not payload["is_superuser"]:
            raise AppError("SYSTEM_SELF_DEMOTE", "不能取消自己的超级管理员身份", 409)
        if not payload["is_superuser"] and user.is_superuser:
            await _last_superuser_guard(db, user.id)
    if payload.get("status") == "disabled":
        if user.id == ctx.user.id:
            raise AppError("SYSTEM_SELF_DISABLE", "不能禁用自己", 409)
        await _revoke_sessions(db, user.id)
        await db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    for field in ("nickname", "dept_id", "email", "phone", "status", "is_superuser", "remark"):
        if field in payload:
            setattr(user, field, payload[field])
    if role_ids is not None:
        if not ctx.has("system:user:assign-role"):
            raise AppError("SYSTEM_USER_FORBIDDEN", "缺少分配角色权限，请改用角色接口", 403)
        user.roles = await _load_roles_for_assignment(db, ctx, role_ids)
    await _log_operation(db, ctx, module="system", action="user.update", detail=f"user_id={user.id}", method="PUT", path=f"/system/users/{user.id}")
    await _commit(db)
    locations = await ipgeo.locate_many([user.last_login_ip])
    return _user_payload(ctx, user, locations)


async def set_user_status(db: AsyncSession, ctx: AuthContext, user_id: int, status: str) -> dict[str, Any]:
    return await update_user(db, ctx, user_id, UserUpdate(status=status))  # type: ignore[arg-type]


async def delete_user(db: AsyncSession, ctx: AuthContext, user_id: int) -> None:
    user = await db.get(User, user_id)
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    if user.id == ctx.user.id:
        raise AppError("SYSTEM_SELF_DELETE", "不能删除自己", 409)
    if user.username_normalized == USERNAME_ADMIN:
        raise AppError("SYSTEM_BUILTIN_USER", "内置管理员账号不可删除", 409)
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    if user.is_superuser:
        await _last_superuser_guard(db, user.id)
    await _revoke_sessions(db, user.id)
    await _log_operation(db, ctx, module="system", action="user.delete", detail=f"user_id={user.id}", method="DELETE", path=f"/system/users/{user.id}")
    await db.delete(user)
    await _commit(db)


BATCH_ACTIONS = ("enable", "disable", "delete")
MAX_BATCH_IDS = 200


async def batch_users(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    action: str,
    ids: list[int],
) -> dict[str, Any]:
    """批量启停 / 删除用户。

    单条失败不影响其他条目：把成功与失败的 id 分别回传，前端按结果提示，
    而不是整个事务回滚后让用户从头再来。
    """
    if action not in BATCH_ACTIONS:
        raise AppError("SYSTEM_BATCH_ACTION_INVALID", f"不支持的批量操作：{action}", 422)
    if not ids:
        raise AppError("SYSTEM_BATCH_EMPTY", "请先选择要操作的用户", 422)
    if len(ids) > MAX_BATCH_IDS:
        raise AppError("SYSTEM_BATCH_TOO_MANY", f"单次最多操作 {MAX_BATCH_IDS} 个用户", 422)

    succeeded: list[int] = []
    failed: list[dict[str, Any]] = []
    for user_id in dict.fromkeys(ids):
        try:
            if action == "delete":
                await delete_user(db, ctx, user_id)
            elif action == "disable":
                await update_user(db, ctx, user_id, UserUpdate(status="disabled"))  # type: ignore[arg-type]
            else:
                await update_user(db, ctx, user_id, UserUpdate(status="active"))  # type: ignore[arg-type]
            succeeded.append(user_id)
        except AppError as exc:
            failed.append({"id": user_id, "code": exc.code, "message": exc.message})

    await _log_operation(
        db,
        ctx,
        module="system",
        action="user.batch",
        detail=f"action={action} total={len(ids)} ok={len(succeeded)} failed={len(failed)}",
        method="POST",
        path="/system/users/batch",
    )
    await _commit(db)
    return {"action": action, "succeeded": succeeded, "failed": failed}


async def reset_user_password(
    db: AsyncSession, ctx: AuthContext, user_id: int, data: ResetPasswordRequest
) -> None:
    user = await db.get(User, user_id)
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    valid, message = validate_password_strength(
        data.new_password, user.username, min_length=await password_min_length(db)
    )
    if not valid:
        raise AppError("SYSTEM_WEAK_PASSWORD", message, 422)
    user.password_hash = hash_password(data.new_password)
    user.must_change_password = data.must_change_password
    # 重置密码即视为管理员介入：顺手清掉失败计数与锁定截止时间，否则锁定中的账号只能干等窗口过去
    user.failed_login_count = 0
    user.locked_until = None
    user.token_version += 1
    await _revoke_sessions(db, user.id)
    await _log_operation(db, ctx, module="system", action="user.reset-password", detail=f"user_id={user.id}", path=f"/system/users/{user.id}/reset-password")
    await _commit(db)


async def assign_user_roles(
    db: AsyncSession, ctx: AuthContext, user_id: int, data: AssignRolesRequest
) -> dict[str, Any]:
    user = (
        (await db.execute(select(User).options(selectinload(User.roles), selectinload(User.dept)).where(User.id == user_id)))
        .scalars()
        .first()
    )
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    roles = await _load_roles_for_assignment(db, ctx, data.role_ids)
    user.roles = roles
    await _log_operation(db, ctx, module="system", action="user.assign-role", detail=f"user_id={user.id};roles={data.role_ids}", method="PUT", path=f"/system/users/{user.id}/roles")
    await _commit(db)
    locations = await ipgeo.locate_many([user.last_login_ip])
    return _user_payload(ctx, user, locations)


async def _role_user_counts(db: AsyncSession, role_ids: list[int]) -> dict[int, int]:
    if not role_ids:
        return {}
    rows = (
        await db.execute(
            select(user_roles.c.role_id, func.count(user_roles.c.user_id))
            .where(user_roles.c.role_id.in_(role_ids))
            .group_by(user_roles.c.role_id)
        )
    ).all()
    return {int(row[0]): int(row[1]) for row in rows}


async def list_roles(db: AsyncSession, *, keyword: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    statement = select(Role).options(selectinload(Role.permissions), selectinload(Role.departments))
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(or_(func.lower(Role.name).like(like), Role.code.like(like)))
    if status:
        statement = statement.where(Role.status == status)
    roles = (await db.execute(statement.order_by(Role.sort, Role.id))).scalars().all()
    counts = await _role_user_counts(db, [role.id for role in roles])
    return [role_out(role, user_count=counts.get(role.id, 0)) for role in roles]


async def list_role_options(db: AsyncSession) -> list[dict[str, Any]]:
    roles = (
        (
            await db.execute(
                select(Role)
                .where(Role.status == "active")
                .order_by(Role.sort, Role.id)
            )
        )
        .scalars()
        .all()
    )
    # 下拉选项只用于分配角色，不返回权限清单和数据范围，避免额外的授权信息暴露
    return [{"id": role.id, "name": role.name, "code": role.code} for role in roles]


async def get_role(db: AsyncSession, role_id: int) -> dict[str, Any]:
    role = (
        (
            await db.execute(
                select(Role)
                .options(selectinload(Role.permissions), selectinload(Role.departments))
                .where(Role.id == role_id)
            )
        )
        .scalars()
        .first()
    )
    if role is None:
        raise AppError("SYSTEM_ROLE_NOT_FOUND", "角色不存在", 404)
    counts = await _role_user_counts(db, [role.id])
    return role_out(role, user_count=counts.get(role.id, 0))


async def _ensure_data_scope_grantable(
    db: AsyncSession, ctx: AuthContext, data_scope: str, dept_ids: list[int]
) -> list[Dept]:
    if ctx.is_superuser:
        depts = list(
            (await db.execute(select(Dept).where(Dept.id.in_(set(dept_ids))))).scalars().all()
        ) if dept_ids else []
        if len(depts) != len(set(dept_ids)):
            raise AppError("SYSTEM_DEPT_NOT_FOUND", "部分部门不存在", 404)
        return depts
    scope = await _user_scope(db, ctx)
    if data_scope == "all" and not scope.all_access:
        raise AppError("SYSTEM_ROLE_ESCALATION", "不能授予全部数据范围", 403)
    if data_scope == "dept_and_below" and not scope.all_access:
        raise AppError("SYSTEM_ROLE_ESCALATION", "不能授予本部门及以下数据范围", 403)
    if data_scope == "custom":
        if not scope.all_access and not set(dept_ids).issubset(scope.dept_ids):
            raise AppError("SYSTEM_ROLE_ESCALATION", "指定部门超出你的数据范围", 403)
    depts = list((await db.execute(select(Dept).where(Dept.id.in_(set(dept_ids))))).scalars().all()) if dept_ids else []
    if len(depts) != len(set(dept_ids)):
        raise AppError("SYSTEM_DEPT_NOT_FOUND", "部分部门不存在", 404)
    return depts


async def create_role(db: AsyncSession, ctx: AuthContext, data: RoleCreate) -> dict[str, Any]:
    if not ctx.is_superuser:
        scope = await _user_scope(db, ctx)
        if not scope.all_access and data.data_scope not in {"self", "dept"}:
            raise AppError("SYSTEM_ROLE_ESCALATION", "不能创建超出自身数据范围的角色", 403)
    exists = (await db.execute(select(Role.id).where(Role.code == data.code))).scalar_one_or_none()
    if exists:
        raise AppError("SYSTEM_ROLE_EXISTS", "角色编码已存在", 409)
    role = Role(
        name=data.name,
        code=data.code,
        data_scope=data.data_scope,
        status=data.status,
        sort=data.sort,
        remark=data.remark,
    )
    db.add(role)
    await _log_operation(db, ctx, module="system", action="role.create", detail=f"role={data.code}", path="/system/roles")
    await _commit(db)
    return await get_role(db, role.id)


async def update_role(db: AsyncSession, ctx: AuthContext, role_id: int, data: RoleUpdate) -> dict[str, Any]:
    role = (
        (
            await db.execute(
                select(Role).options(selectinload(Role.permissions), selectinload(Role.departments)).where(Role.id == role_id)
            )
        )
        .scalars()
        .first()
    )
    if role is None:
        raise AppError("SYSTEM_ROLE_NOT_FOUND", "角色不存在", 404)
    payload = data.model_dump(exclude_unset=True)
    if role.is_builtin and role.code == "super_admin":
        for field in ("status", "data_scope"):
            if field in payload and payload[field] != getattr(role, field):
                raise AppError("SYSTEM_ROLE_BUILTIN", "内置超级管理员角色不可停用或改变数据范围", 409)
    if "data_scope" in payload and not ctx.is_superuser:
        if payload["data_scope"] not in {"self", "dept"}:
            raise AppError("SYSTEM_ROLE_ESCALATION", "不能授予超出自身数据范围的角色", 403)
    for field in ("name", "data_scope", "status", "sort", "remark"):
        if field in payload:
            setattr(role, field, payload[field])
    await _log_operation(db, ctx, module="system", action="role.update", detail=f"role_id={role.id}", method="PUT", path=f"/system/roles/{role.id}")
    await _commit(db)
    return role_out(role)


async def delete_role(db: AsyncSession, ctx: AuthContext, role_id: int) -> None:
    role = await db.get(Role, role_id)
    if role is None:
        raise AppError("SYSTEM_ROLE_NOT_FOUND", "角色不存在", 404)
    if role.is_builtin:
        raise AppError("SYSTEM_ROLE_BUILTIN", "内置角色不可删除", 409)
    count = int(
        (await db.execute(select(func.count(user_roles.c.user_id)).where(user_roles.c.role_id == role_id))).scalar() or 0
    )
    if count:
        raise AppError("SYSTEM_ROLE_IN_USE", "角色仍被用户引用，无法删除", 409)
    await _log_operation(db, ctx, module="system", action="role.delete", detail=f"role_id={role_id}", method="DELETE", path=f"/system/roles/{role_id}")
    await db.delete(role)
    await _commit(db)


async def assign_role_permissions(
    db: AsyncSession, ctx: AuthContext, role_id: int, data: AssignPermissionsRequest
) -> dict[str, Any]:
    role = (
        (
            await db.execute(
                select(Role).options(selectinload(Role.permissions), selectinload(Role.departments)).where(Role.id == role_id)
            )
        )
        .scalars()
        .first()
    )
    if role is None:
        raise AppError("SYSTEM_ROLE_NOT_FOUND", "角色不存在", 404)
    permissions = (
        (
            await db.execute(
                select(Permission).where(
                    Permission.code.in_(set(data.permission_codes)),
                    Permission.deprecated_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(permissions) != len(set(data.permission_codes)):
        raise AppError("SYSTEM_PERMISSION_NOT_FOUND", "部分权限不存在或已弃用", 404)
    if not ctx.is_superuser:
        codes = {item.code for item in permissions}
        if not codes.issubset(ctx.permissions):
            raise AppError("SYSTEM_ROLE_ESCALATION", "不能授予超过自己的权限", 403)
    role.permissions = list(permissions)
    await _log_operation(db, ctx, module="system", action="role.assign-permission", detail=f"role_id={role.id};count={len(permissions)}", method="PUT", path=f"/system/roles/{role.id}/permissions")
    await _commit(db)
    return role_out(role)


async def set_role_data_scope(
    db: AsyncSession, ctx: AuthContext, role_id: int, data: DataScopeUpdate
) -> dict[str, Any]:
    role = (
        (
            await db.execute(
                select(Role).options(selectinload(Role.permissions), selectinload(Role.departments)).where(Role.id == role_id)
            )
        )
        .scalars()
        .first()
    )
    if role is None:
        raise AppError("SYSTEM_ROLE_NOT_FOUND", "角色不存在", 404)
    if role.is_builtin:
        raise AppError("SYSTEM_ROLE_BUILTIN", "内置角色数据范围不可修改", 409)
    depts = await _ensure_data_scope_grantable(db, ctx, data.data_scope, data.dept_ids if data.data_scope == "custom" else [])
    role.data_scope = data.data_scope
    role.departments = depts
    await _log_operation(db, ctx, module="system", action="role.data-scope", detail=f"role_id={role.id};scope={role.data_scope}", method="PUT", path=f"/system/roles/{role.id}/data-scope")
    await _commit(db)
    return role_out(role)


async def list_permissions(db: AsyncSession, *, include_deprecated: bool = False) -> dict[str, Any]:
    statement = select(Permission).order_by(Permission.module, Permission.code)
    if not include_deprecated:
        statement = statement.where(Permission.deprecated_at.is_(None))
    permissions = (await db.execute(statement)).scalars().all()
    modules: dict[str, list[dict[str, Any]]] = {}
    for item in permissions:
        modules.setdefault(item.module, []).append(permission_out(item))
    return {
        "items": [permission_out(item) for item in permissions],
        "modules": [{"module": module, "items": items} for module, items in modules.items()],
    }


def _build_tree(
    items: Sequence[Menu],
    *,
    authorized: set[str] | None = None,
    parent_id: int | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda row: (row.sort, row.id)):
        if item.parent_id != parent_id:
            continue
        if item.type == "button":
            continue
        if authorized is not None and item.permission_code and item.permission_code not in authorized:
            continue
        children = _build_tree(items, authorized=authorized, parent_id=item.id)
        if item.type == "directory" and not children:
            continue
        result.append(menu_out(item, children))
    return result


async def list_menus(db: AsyncSession) -> list[dict[str, Any]]:
    menus = (await db.execute(select(Menu).order_by(Menu.sort, Menu.id))).scalars().all()
    all_tree = _build_tree_for_admin(menus)
    return all_tree


def _build_tree_for_admin(items: Sequence[Menu], parent_id: int | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda row: (row.sort, row.id)):
        if item.parent_id != parent_id:
            continue
        children = _build_tree_for_admin(items, item.id)
        result.append(menu_out(item, children))
    return result


async def current_user_menu(db: AsyncSession, ctx: AuthContext) -> list[dict[str, Any]]:
    if not ctx.is_admin:
        return []
    menus = (
        (
            await db.execute(
                select(Menu).where(Menu.status == "active").order_by(Menu.sort, Menu.id)
            )
        )
        .scalars()
        .all()
    )
    return _build_tree(menus, authorized=None if ctx.is_superuser else ctx.permissions)


async def _validate_menu_payload(
    db: AsyncSession, *, menu_id: int | None, parent_id: int | None, menu_type: str, path: str | None, permission_code: str | None
) -> None:
    if parent_id is not None:
        parent = await db.get(Menu, parent_id)
        if parent is None:
            raise AppError("SYSTEM_MENU_PARENT_NOT_FOUND", "上级菜单不存在", 404)
        if parent.type == "button":
            raise AppError("SYSTEM_MENU_PARENT_INVALID", "按钮不能作为上级菜单", 422)
        if menu_id is not None:
            # 用已访问集合兜底：库里若已存在环（导入或历史脏数据），向上遍历必须终止
            seen: set[int] = {menu_id}
            cursor: Menu | None = parent
            while cursor is not None:
                if cursor.id in seen:
                    raise AppError("SYSTEM_MENU_CYCLE", "菜单上级不能形成循环", 422)
                seen.add(cursor.id)
                cursor = await db.get(Menu, cursor.parent_id) if cursor.parent_id else None
    if menu_type in {"directory", "menu"} and not path:
        raise AppError("SYSTEM_MENU_PATH_REQUIRED", "目录或菜单必须设置路由路径", 422)
    if menu_type == "button" and not permission_code:
        raise AppError("SYSTEM_MENU_PERMISSION_REQUIRED", "按钮必须绑定权限码", 422)
    if permission_code:
        permission = (await db.execute(select(Permission.id).where(Permission.code == permission_code))).scalar_one_or_none()
        if permission is None:
            raise AppError("SYSTEM_PERMISSION_NOT_FOUND", "权限码不存在", 404)
    if path:
        duplicate = (
            await db.execute(select(Menu.id).where(Menu.path == path, Menu.id != (menu_id or 0)))
        ).scalar_one_or_none()
        if duplicate:
            raise AppError("SYSTEM_MENU_PATH_EXISTS", "路由路径已存在", 409)


async def create_menu(db: AsyncSession, ctx: AuthContext, data: MenuCreate) -> dict[str, Any]:
    await _validate_menu_payload(
        db,
        menu_id=None,
        parent_id=data.parent_id,
        menu_type=data.type,
        path=data.path,
        permission_code=data.permission_code,
    )
    menu = Menu(**data.model_dump(), is_builtin=False)
    db.add(menu)
    await _log_operation(db, ctx, module="system", action="menu.create", detail=f"menu={data.name}", path="/system/menus")
    await _commit(db)
    await db.refresh(menu)
    return menu_out(menu)


async def update_menu(db: AsyncSession, ctx: AuthContext, menu_id: int, data: MenuUpdate) -> dict[str, Any]:
    menu = await db.get(Menu, menu_id)
    if menu is None:
        raise AppError("SYSTEM_MENU_NOT_FOUND", "菜单不存在", 404)
    payload = data.model_dump(exclude_unset=True)
    if "type" in payload and payload["type"] != menu.type:
        child_count = int(
            (await db.execute(select(func.count(Menu.id)).where(Menu.parent_id == menu_id))).scalar() or 0
        )
        if child_count:
            raise AppError("SYSTEM_MENU_HAS_CHILDREN", "存在子节点时不能修改类型", 409)
    await _validate_menu_payload(
        db,
        menu_id=menu_id,
        parent_id=payload.get("parent_id", menu.parent_id),
        menu_type=payload.get("type", menu.type),
        path=payload.get("path", menu.path) if "path" in payload else menu.path,
        permission_code=payload.get("permission_code", menu.permission_code),
    )
    for field, value in payload.items():
        setattr(menu, field, value)
    await _log_operation(db, ctx, module="system", action="menu.update", detail=f"menu_id={menu.id}", method="PUT", path=f"/system/menus/{menu.id}")
    await _commit(db)
    return menu_out(menu)


async def delete_menu(db: AsyncSession, ctx: AuthContext, menu_id: int) -> dict[str, Any]:
    menu = await db.get(Menu, menu_id)
    if menu is None:
        raise AppError("SYSTEM_MENU_NOT_FOUND", "菜单不存在", 404)
    child_count = int((await db.execute(select(func.count(Menu.id)).where(Menu.parent_id == menu_id))).scalar() or 0)
    if child_count:
        raise AppError("SYSTEM_MENU_HAS_CHILDREN", "存在子节点，无法删除", 409)
    if menu.is_builtin:
        raise AppError("SYSTEM_MENU_BUILTIN", "内置菜单不可删除，可停用", 409)
    if menu.permission_code:
        menu.status = "disabled"
        await _log_operation(db, ctx, module="system", action="menu.disable", detail=f"menu_id={menu.id}", method="DELETE", path=f"/system/menus/{menu.id}")
        await _commit(db)
        return {"disabled": True}
    await _log_operation(db, ctx, module="system", action="menu.delete", detail=f"menu_id={menu_id}", method="DELETE", path=f"/system/menus/{menu_id}")
    await db.delete(menu)
    await _commit(db)
    return {"deleted": True}


def _build_dept_tree(items: list[Dept], parent_id: int | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda row: (row.sort, row.id)):
        if item.parent_id != parent_id:
            continue
        result.append(dept_out(item, _build_dept_tree(items, item.id)))
    return result


def _visible_dept_ids(depts: list[Dept], scope: DataScope) -> set[int]:
    paths = [item.ancestors for item in depts if item.id in scope.dept_ids]
    return {
        item.id
        for item in depts
        if item.id in scope.dept_ids or any(f"/{item.id}/" in path for path in paths)
    }


async def _dept_scope(db: AsyncSession, ctx: AuthContext) -> DataScope:
    if ctx.is_superuser:
        return DataScope(all_access=True)
    return await get_data_scope(db, ctx)


async def list_depts(db: AsyncSession, ctx: AuthContext) -> list[dict[str, Any]]:
    depts = list((await db.execute(select(Dept).order_by(Dept.sort, Dept.id))).scalars().all())
    scope = await _dept_scope(db, ctx)
    if not scope.all_access:
        visible = _visible_dept_ids(depts, scope)
        depts = [item for item in depts if item.id in visible]
    return _build_dept_tree(depts)


async def create_dept(db: AsyncSession, ctx: AuthContext, data: DeptCreate) -> dict[str, Any]:
    parent = None
    if data.parent_id:
        parent = await db.get(Dept, data.parent_id)
        if parent is None:
            raise AppError("SYSTEM_DEPT_NOT_FOUND", "上级部门不存在", 404)
    scope = await _dept_scope(db, ctx)
    if not scope.all_access and (not data.parent_id or data.parent_id not in scope.dept_ids):
        raise AppError("SYSTEM_DEPT_FORBIDDEN", "上级部门不在你的数据范围内", 403)
    dept = Dept(
        **data.model_dump(),
        ancestors=(parent.ancestors + f"{parent.id}/") if parent else "/",
    )
    db.add(dept)
    await _log_operation(db, ctx, module="system", action="dept.create", detail=f"dept={data.name}", path="/system/depts")
    await _commit(db)
    await db.refresh(dept)
    return dept_out(dept)


async def update_dept(db: AsyncSession, ctx: AuthContext, dept_id: int, data: DeptUpdate) -> dict[str, Any]:
    dept = await db.get(Dept, dept_id)
    if dept is None:
        raise AppError("SYSTEM_DEPT_NOT_FOUND", "部门不存在", 404)
    scope = await _dept_scope(db, ctx)
    if not scope.all_access:
        if dept_id not in scope.dept_ids:
            raise AppError("SYSTEM_DEPT_FORBIDDEN", "无权修改该部门", 403)
    payload = data.model_dump(exclude_unset=True)
    old_prefix = f"{dept.ancestors}{dept.id}/"
    if "parent_id" in payload and payload["parent_id"] != dept.parent_id:
        new_parent_id = payload["parent_id"]
        if not scope.all_access and (not new_parent_id or new_parent_id not in scope.dept_ids):
            raise AppError("SYSTEM_DEPT_FORBIDDEN", "上级部门不在你的数据范围内", 403)
        if new_parent_id == dept.id:
            raise AppError("SYSTEM_DEPT_CYCLE", "部门上级不能是自己", 422)
        new_ancestors = "/"
        if new_parent_id:
            parent = await db.get(Dept, new_parent_id)
            if parent is None:
                raise AppError("SYSTEM_DEPT_NOT_FOUND", "上级部门不存在", 404)
            if parent.ancestors.startswith(old_prefix):
                raise AppError("SYSTEM_DEPT_CYCLE", "部门上级不能是自己的下级", 422)
            new_ancestors = parent.ancestors + f"{parent.id}/"
        dept.ancestors = new_ancestors
        dept.parent_id = new_parent_id
        await db.execute(
            update(Dept)
            .where(Dept.ancestors.like(f"{old_prefix}%"))
            .values(ancestors=func.replace(Dept.ancestors, old_prefix, f"{new_ancestors}{dept.id}/"))
        )
    for field in ("name", "leader", "phone", "sort", "status", "remark"):
        if field in payload:
            setattr(dept, field, payload[field])
    await _log_operation(db, ctx, module="system", action="dept.update", detail=f"dept_id={dept.id}", method="PUT", path=f"/system/depts/{dept.id}")
    await _commit(db)
    return dept_out(dept)


async def delete_dept(db: AsyncSession, ctx: AuthContext, dept_id: int) -> None:
    dept = await db.get(Dept, dept_id)
    if dept is None:
        raise AppError("SYSTEM_DEPT_NOT_FOUND", "部门不存在", 404)
    scope = await _dept_scope(db, ctx)
    if not scope.all_access and dept_id not in scope.dept_ids:
        raise AppError("SYSTEM_DEPT_FORBIDDEN", "无权删除该部门", 403)
    child_count = int((await db.execute(select(func.count(Dept.id)).where(Dept.parent_id == dept_id))).scalar() or 0)
    if child_count:
        raise AppError("SYSTEM_DEPT_HAS_CHILDREN", "存在子部门，无法删除", 409)
    user_count = int((await db.execute(select(func.count(User.id)).where(User.dept_id == dept_id))).scalar() or 0)
    if user_count:
        raise AppError("SYSTEM_DEPT_IN_USE", "部门下存在用户，无法删除", 409)
    role_count = int(
        (await db.execute(select(func.count(role_depts.c.role_id)).where(role_depts.c.dept_id == dept_id))).scalar() or 0
    )
    if role_count:
        raise AppError("SYSTEM_DEPT_IN_USE", "部门被角色的自定义数据范围引用，请先调整角色", 409)
    await _log_operation(db, ctx, module="system", action="dept.delete", detail=f"dept_id={dept_id}", method="DELETE", path=f"/system/depts/{dept_id}")
    await db.delete(dept)
    await _commit(db)


async def update_profile(db: AsyncSession, ctx: AuthContext, data: ProfileUpdate) -> dict[str, Any]:
    user = ctx.user
    values = data.model_dump(exclude_unset=True)
    avatar_file_id = values.get("avatar_file_id")
    if avatar_file_id is not None:
        item = await db.get(FileObject, avatar_file_id)
        if item is None or item.uploader_id != user.id:
            raise AppError("SYSTEM_PROFILE_AVATAR_INVALID", "头像文件不存在", 422)
        if not is_safe_image(item):
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
        raise AppError("AUTH_SESSION_NOT_FOUND", "当前身份没有会话", 400)
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
    await db.commit()
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
    await db.commit()


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
    await db.commit()


# 用户导入支持的中英文表头。规范字段名与 UserCreate 一致，
# 运营无论按英文模板还是中文模板填都能导入。
USER_IMPORT_COLUMNS: dict[str, str] = {
    "username": "username",
    "用户名": "username",
    "password": "password",
    "密码": "password",
    "nickname": "nickname",
    "昵称": "nickname",
    "dept_id": "dept_id",
    "部门id": "dept_id",
    "email": "email",
    "邮箱": "email",
    "phone": "phone",
    "手机号": "phone",
    "status": "status",
    "状态": "status",
    "role_ids": "role_ids",
    "角色id": "role_ids",
    "remark": "remark",
    "备注": "remark",
    "must_change_password": "must_change_password",
    "首次登录改密": "must_change_password",
}
USER_IMPORT_REQUIRED: tuple[str, ...] = ("username", "password", "nickname")


def _user_create_from_row(row: dict[str, str]) -> UserCreate:
    """把一行 CSV 转成 ``UserCreate``；字段级错误在这里就变成行号可定位的失败。"""
    payload: dict[str, Any] = {
        "username": row.get("username", ""),
        "password": row.get("password", ""),
        "nickname": row.get("nickname", ""),
        "dept_id": optional_int(row.get("dept_id")),
        "email": optional_text(row.get("email")),
        "phone": optional_text(row.get("phone")),
        "status": row.get("status") or "active",
        "role_ids": split_ids(row.get("role_ids")),
        "remark": optional_text(row.get("remark")),
        "must_change_password": (row.get("must_change_password") or "").lower()
        not in ("false", "0", "否", "no"),
    }
    try:
        return UserCreate(**payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ()))
        label = field or "整行"
        raise AppError("IMPORT_ROW_INVALID", f"字段 {label}：{first.get('msg', '格式不正确')}", 422) from exc


async def import_users(
    db: AsyncSession,
    ctx: AuthContext,
    raw: bytes,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """批量导入用户。

    两条设计取舍：

    - **逐行调用 ``create_user``** 而不是自己再实现一遍校验。用户名查重、
      密码强度、部门与角色越权、超级管理员权限全部复用同一套逻辑，
      不会出现「导入放过的用户，单个创建时反而被拒」这种割裂。
      代价是每行一次 commit，500 行的量级下完全可以接受。
    - **单行失败不回滚整批**：第 37 行密码太弱不该让前 36 行白导入。
      失败的的行号、用户名、原因都原样返回，用户改完 CSV 可以只重传。

    ``dry_run`` 走完整写入路径但不提交，最后统一 rollback，因此能发现
    真实写入才会暴露的问题；代价是预检期间会话处于未提交状态，
      不能和别的请求复用同一个 session。
    """
    rows = read_rows(
        raw, columns=USER_IMPORT_COLUMNS, required=USER_IMPORT_REQUIRED
    )
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for offset, row in enumerate(rows):
        # 表头占第 1 行，数据从第 2 行开始，报行号时按 CSV 里的真实行号算
        line = offset + 2
        username = row.get("username", "")
        try:
            data = _user_create_from_row(row)
        except AppError as exc:
            failed.append({"row": line, "username": username, "code": exc.code, "message": exc.message})
            continue
        try:
            created = await create_user(db, ctx, data, commit=not dry_run)
        except AppError as exc:
            # 失败行的 session 可能停在半完成状态，先回滚再继续，
            # 否则下一行会踩在上一个失败事务的残留上
            await db.rollback()
            failed.append({"row": line, "username": data.username, "code": exc.code, "message": exc.message})
            continue
        succeeded.append({"row": line, "username": created["username"], "id": created["id"]})

    if dry_run:
        await db.rollback()

    if not dry_run:
        await _log_operation(
            db,
            ctx,
            module="system",
            action="user.import",
            detail=f"total={len(rows)} ok={len(succeeded)} failed={len(failed)}",
            path="/system/users/import",
        )
        await db.commit()

    return {
        "total": len(rows),
        "succeeded": succeeded,
        "failed": failed,
        "dry_run": dry_run,
    }


__all__ = [
    "assign_role_permissions",
    "assign_user_roles",
    "cleanup_old_sessions",
    "create_dept",
    "create_menu",
    "create_role",
    "create_user",
    "current_user_menu",
    "delete_dept",
    "delete_menu",
    "delete_role",
    "delete_user",
    "get_role",
    "get_user",
    "import_users",
    "kick_session",
    "list_depts",
    "list_menus",
    "list_permissions",
    "list_role_options",
    "list_roles",
    "list_user_sessions",
    "list_users",
    "reset_user_password",
    "revoke_other_sessions",
    "revoke_user_session",
    "set_role_data_scope",
    "set_user_status",
    "update_dept",
    "update_menu",
    "update_profile",
    "update_role",
    "update_user",
    "write_login_log",
]
