from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.authz import AuthContext
from app.core.errors import AppError
from app.core.search import LIKE_ESCAPE, contains
from app.kernel.db import commit as _commit
from app.kernel.db import log_operation as _log_operation
from app.kernel.db import transactional
from app.kernel.models import Dept, Permission, Role, user_roles
from app.kernel.scope import user_scope as _user_scope
from app.modules.sys.system.schemas import AssignPermissionsRequest, DataScopeUpdate, RoleCreate, RoleUpdate
from app.modules.sys.system.serializers import role_out


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
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(Role.name).like(like, escape=LIKE_ESCAPE),
                Role.code.like(like, escape=LIKE_ESCAPE),
            )
        )
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
    if data_scope == "custom" and not scope.all_access and not set(dept_ids).issubset(scope.dept_ids):
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
        raise AppError("SYSTEM_ROLE_EXISTS", status_code=409)
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
    if "data_scope" in payload and not ctx.is_superuser and payload["data_scope"] not in {"self", "dept"}:
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
        raise AppError("SYSTEM_ROLE_IN_USE", status_code=409)
    await _log_operation(db, ctx, module="system", action="role.delete", detail=f"role_id={role_id}", method="DELETE", path=f"/system/roles/{role_id}")
    await db.delete(role)
    await _commit(db)


@transactional
async def assign_role_permissions(
    db: AsyncSession, ctx: AuthContext, role_id: int, data: AssignPermissionsRequest
) -> dict[str, Any]:
    """整体替换角色权限：要么全换，要么不动。

    权限集合是覆盖写，中途失败会留下「既不是旧集合也不是新集合」的权限边界——
    这正是越权的温床，所以整段必须原子。
    """
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
