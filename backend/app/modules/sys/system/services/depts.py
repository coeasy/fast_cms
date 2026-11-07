from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, DataScope, get_data_scope
from app.core.errors import AppError
from app.kernel.models import Dept, User, role_depts
from app.modules.sys.system.schemas import DeptCreate, DeptUpdate
from app.modules.sys.system.serializers import dept_out
from app.modules.sys.system.services._common import (
    MAX_DEPT_ANCESTORS_LEN,
    _commit,
    _log_operation,
    _validate_dept_path,
)


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
    ancestors = (parent.ancestors + f"{parent.id}/") if parent else "/"
    _validate_dept_path(ancestors)
    dept = Dept(
        **data.model_dump(),
        ancestors=ancestors,
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
    if not scope.all_access and dept_id not in scope.dept_ids:
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
        # 子树整体平移：所有后代 ancestors 前缀从 old_prefix 换成 new_prefix。
        # 移动前先校验最深后代不会突破路径上限（P1-7）。
        descendant_ancestors = (
            (await db.execute(select(Dept.ancestors).where(Dept.ancestors.like(f"{old_prefix}%"))))
            .scalars()
            .all()
        )
        delta = len(f"{new_ancestors}{dept.id}/") - len(old_prefix)
        deepest = max((len(item) for item in descendant_ancestors), default=len(old_prefix))
        if deepest + delta > MAX_DEPT_ANCESTORS_LEN:
            raise AppError(
                "SYSTEM_DEPT_TOO_DEEP",
                f"移动后存在部门路径长度超过上限 {MAX_DEPT_ANCESTORS_LEN}",
                422,
            )
        _validate_dept_path(f"{new_ancestors}{dept.id}/")
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
        raise AppError("SYSTEM_DEPT_HAS_CHILDREN", status_code=409)
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
