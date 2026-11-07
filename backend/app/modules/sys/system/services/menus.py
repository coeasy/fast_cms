from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext
from app.core.errors import AppError
from app.kernel.db import commit as _commit
from app.kernel.db import log_operation as _log_operation
from app.kernel.models import Menu, Permission
from app.modules.sys.system.schemas import MenuCreate, MenuUpdate
from app.modules.sys.system.serializers import menu_out, permission_out


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
    return _build_tree_for_admin(menus)


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
    return _build_tree(
        menus,
        # API Key 的 "*" 与超管等价：只判 is_superuser 会让它拿到空菜单树，
        # 排查起来像是「权限没生效」，实际是通配符没被当成通配符。
        authorized=None if (ctx.is_superuser or "*" in ctx.permissions) else ctx.permissions,
    )


async def _validate_menu_payload(
    db: AsyncSession, *, menu_id: int | None, parent_id: int | None, menu_type: str, path: str | None, permission_code: str | None
) -> None:
    if parent_id is not None:
        parent = await db.get(Menu, parent_id)
        if parent is None:
            raise AppError("SYSTEM_MENU_PARENT_NOT_FOUND", status_code=404)
        if parent.type == "button":
            raise AppError("SYSTEM_MENU_PARENT_INVALID", status_code=422)
        if menu_id is not None:
            # 用已访问集合兜底：库里若已存在环（导入或历史脏数据），向上遍历必须终止
            seen: set[int] = {menu_id}
            cursor: Menu | None = parent
            while cursor is not None:
                if cursor.id in seen:
                    raise AppError("SYSTEM_MENU_CYCLE", status_code=422)
                seen.add(cursor.id)
                cursor = await db.get(Menu, cursor.parent_id) if cursor.parent_id else None
    if menu_type in {"directory", "menu"} and not path:
        raise AppError("SYSTEM_MENU_PATH_REQUIRED", status_code=422)
    if menu_type == "button" and not permission_code:
        raise AppError("SYSTEM_MENU_PERMISSION_REQUIRED", status_code=422)
    if permission_code:
        permission = (await db.execute(select(Permission.id).where(Permission.code == permission_code))).scalar_one_or_none()
        if permission is None:
            raise AppError("SYSTEM_PERMISSION_NOT_FOUND", "权限码不存在", 404)
    if path:
        duplicate = (
            await db.execute(select(Menu.id).where(Menu.path == path, Menu.id != (menu_id or 0)))
        ).scalar_one_or_none()
        if duplicate:
            raise AppError("SYSTEM_MENU_PATH_EXISTS", status_code=409)


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
        raise AppError("SYSTEM_MENU_NOT_FOUND", status_code=404)
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
        raise AppError("SYSTEM_MENU_NOT_FOUND", status_code=404)
    child_count = int((await db.execute(select(func.count(Menu.id)).where(Menu.parent_id == menu_id))).scalar() or 0)
    if child_count:
        raise AppError("SYSTEM_MENU_HAS_CHILDREN", "存在子节点，无法删除", 409)
    if menu.is_builtin:
        raise AppError("SYSTEM_MENU_BUILTIN", status_code=409)
    if menu.permission_code:
        menu.status = "disabled"
        await _log_operation(db, ctx, module="system", action="menu.disable", detail=f"menu_id={menu.id}", method="DELETE", path=f"/system/menus/{menu.id}")
        await _commit(db)
        return {"disabled": True}
    await _log_operation(db, ctx, module="system", action="menu.delete", detail=f"menu_id={menu_id}", method="DELETE", path=f"/system/menus/{menu_id}")
    await db.delete(menu)
    await _commit(db)
    return {"deleted": True}
