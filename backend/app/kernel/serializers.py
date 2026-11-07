"""内核级序列化：被多个模块（monitor / export / audit / auth）共用的**实体投影**。

与 ``app/core/serializers.py`` 的分工：那边只有 ``iso()`` 这类「值 → 值」的纯格式化，
不认识 ORM；这边接收整行实体，决定挑哪些字段、怎么拼关联、按权限置空哪些敏感项。
判据是「需不需要整行实体」——需要就在这边。

只放跨模块复用的部分；``role_out`` / ``menu_out`` / ``dept_out`` 这类
只在系统管理内部使用的仍留在 ``app.modules.sys.system.serializers``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core import ipgeo
from app.core.serializers import iso
from app.kernel.models import Role, User, UserSession


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
        # （由调用方按 monitor:login-log:list 权限置空，见 user_payload）
        "last_login_location": ipgeo.label_of(locations, user.last_login_ip),
        "remark": user.remark,
        "created_at": iso(user.created_at),
        "updated_at": iso(user.updated_at),
    }
    if include_roles:
        data["roles"] = [role_brief(role) for role in user.roles]
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
