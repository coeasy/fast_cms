from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core import ipgeo
from app.core.serializers import iso
from app.modules.system.models import Dept, Menu, Permission, Role, User, UserSession


def role_brief(role: Role) -> dict[str, Any]:
    return {"id": role.id, "name": role.name, "code": role.code}


def user_brief(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname,
        "dept_id": user.dept_id,
        "dept_name": user.dept.name if user.dept else None,
        "status": user.status,
        "is_superuser": user.is_superuser,
    }


def user_out(
    user: User,
    *,
    include_roles: bool = True,
    locations: Mapping[str, ipgeo.IpLocation | None] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        **user_brief(user),
        "email": user.email,
        "phone": user.phone,
        "avatar_file_id": user.avatar_file_id,
        "must_change_password": user.must_change_password,
        # 连续登录失败后账号会临时锁定，管理端需要看到锁定截止时间才能判断该走重置密码
        "locked_until": iso(user.locked_until),
        "last_login_at": iso(user.last_login_at),
        "last_login_ip": user.last_login_ip,
        # 归属地跟随 IP 的可见性：看不到 IP 的人也不该看到 IP 在哪
        # （由调用方按 monitor:login-log:list 权限置空，见 _user_payload）
        "last_login_location": ipgeo.label_of(locations, user.last_login_ip),
        "remark": user.remark,
        "created_at": iso(user.created_at),
        "updated_at": iso(user.updated_at),
    }
    if include_roles:
        data["roles"] = [role_brief(role) for role in user.roles]
    return data


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


def session_out(session: UserSession, *, current: bool = False) -> dict[str, Any]:
    return {
        "id": session.id,
        "ip": session.ip,
        "user_agent": session.user_agent,
        "last_seen_at": iso(session.last_seen_at),
        "created_at": iso(session.created_at),
        "expires_at": iso(session.expires_at),
        "current": current,
    }
