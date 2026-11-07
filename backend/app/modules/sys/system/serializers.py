from __future__ import annotations

from typing import Any

from app.core.serializers import iso
from app.kernel.models import Dept, Menu, Permission, Role
from app.kernel.serializers import role_brief, session_out, user_brief, user_out

# 跨模块复用的序列化（user/session）已下沉到 app.kernel.serializers，
# 此处 re-export 保持既有导入路径可用。
__all__ = [
    "dept_out",
    "menu_out",
    "permission_out",
    "role_brief",
    "role_out",
    "session_out",
    "user_brief",
    "user_out",
]


def role_out(role: Role, *, user_count: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": role.id,
        "name": role.name,
        "code": role.code,
        "data_scope": role.data_scope,
        "status": role.status,
        "sort": role.sort,
        "is_builtin": role.is_builtin,
        "remark": role.remark,
        "permission_codes": sorted(item.code for item in role.permissions),
        "dept_ids": [dept.id for dept in role.departments],
        "created_at": iso(role.created_at),
        "updated_at": iso(role.updated_at),
    }
    if user_count is not None:
        data["user_count"] = user_count
    return data


def permission_out(permission: Permission) -> dict[str, Any]:
    return {
        "id": permission.id,
        "code": permission.code,
        "name": permission.name,
        "module": permission.module,
        "type": permission.type,
        "description": permission.description,
        "deprecated_at": iso(permission.deprecated_at),
        "created_at": iso(permission.created_at),
    }


def menu_out(menu: Menu, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": menu.id,
        "parent_id": menu.parent_id,
        "type": menu.type,
        "name": menu.name,
        "route_name": menu.route_name,
        "path": menu.path,
        "component_key": menu.component_key,
        "redirect": menu.redirect,
        "icon": menu.icon,
        "sort": menu.sort,
        "visible": menu.visible,
        "status": menu.status,
        "permission_code": menu.permission_code,
        "is_builtin": menu.is_builtin,
        "remark": menu.remark,
        "created_at": iso(menu.created_at),
        "updated_at": iso(menu.updated_at),
    }
    if children is not None:
        data["children"] = children
    return data


def dept_out(dept: Dept, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": dept.id,
        "parent_id": dept.parent_id,
        "ancestors": dept.ancestors,
        "name": dept.name,
        "leader": dept.leader,
        "phone": dept.phone,
        "sort": dept.sort,
        "status": dept.status,
        "remark": dept.remark,
        "created_at": iso(dept.created_at),
        "updated_at": iso(dept.updated_at),
    }
    if children is not None:
        data["children"] = children
    return data


