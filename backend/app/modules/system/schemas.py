from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Status = Literal["active", "disabled"]
MenuType = Literal["directory", "menu", "button"]
DataScopeType = Literal["all", "dept", "dept_and_below", "self", "custom"]

# 菜单路径只能是指向站内路由；带协议、// 开头或 query 的写法会在浏览器里变成跳转或脚本入口
MENU_PATH_PATTERN = re.compile(r"^/(?!/)[A-Za-z0-9_./-]*$")


def _validate_menu_path(value: str | None) -> str | None:
    if not value:
        # 菜单可以没有路径（按钮、纯分组），清空字段时前端传空串
        return None
    if not MENU_PATH_PATTERN.match(value):
        raise ValueError("菜单路径和重定向必须是以 / 开头的站内路由，不能含协议、// 开头或查询串")
    return value


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    nickname: str = Field(min_length=1, max_length=64)
    dept_id: int | None = None
    email: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    status: Status = "active"
    is_superuser: bool = False
    must_change_password: bool = True
    role_ids: list[int] = Field(default_factory=list)
    remark: str | None = Field(default=None, max_length=500)


class UserUpdate(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=64)
    dept_id: int | None = None
    email: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    status: Status | None = None
    is_superuser: bool | None = None
    remark: str | None = Field(default=None, max_length=500)
    # 不传即保持原样，传空数组即清空；与基础资料同一事务提交，避免两次请求出现半成品状态
    role_ids: list[int] | None = Field(default=None, max_length=50)


class UserStatusUpdate(BaseModel):
    status: Status


class UserBatchRequest(BaseModel):
    """批量操作用户：``action`` 为 enable / disable / delete。"""

    action: str = Field(pattern="^(enable|disable|delete)$")
    ids: list[int] = Field(min_length=1, max_length=200)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)
    must_change_password: bool = True


class AssignRolesRequest(BaseModel):
    role_ids: list[int] = Field(default_factory=list, max_length=50)


class AssignPermissionsRequest(BaseModel):
    permission_codes: list[str] = Field(default_factory=list, max_length=500)


class DataScopeUpdate(BaseModel):
    data_scope: DataScopeType
    dept_ids: list[int] = Field(default_factory=list, max_length=200)


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    data_scope: DataScopeType = "all"
    status: Status = "active"
    sort: int = 0
    remark: str | None = Field(default=None, max_length=500)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    data_scope: DataScopeType | None = None
    status: Status | None = None
    sort: int | None = None
    remark: str | None = Field(default=None, max_length=500)


class MenuCreate(BaseModel):
    parent_id: int | None = None
    type: MenuType = "menu"
    name: str = Field(min_length=1, max_length=64)
    route_name: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=128)
    component_key: str | None = Field(default=None, max_length=128)
    redirect: str | None = Field(default=None, max_length=128)
    icon: str | None = Field(default=None, max_length=64)
    sort: int = 0
    visible: bool = True
    status: Status = "active"
    permission_code: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("path", "redirect")
    @classmethod
    def _check_internal_path(cls, value: str | None) -> str | None:
        return _validate_menu_path(value)


class MenuUpdate(BaseModel):
    parent_id: int | None = None
    type: MenuType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=64)
    route_name: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=128)
    component_key: str | None = Field(default=None, max_length=128)
    redirect: str | None = Field(default=None, max_length=128)
    icon: str | None = Field(default=None, max_length=64)
    sort: int | None = None
    visible: bool | None = None
    status: Status | None = None
    permission_code: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("path", "redirect")
    @classmethod
    def _check_internal_path(cls, value: str | None) -> str | None:
        return _validate_menu_path(value)


class DeptCreate(BaseModel):
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=64)
    leader: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    sort: int = 0
    status: Status = "active"
    remark: str | None = Field(default=None, max_length=500)


class DeptUpdate(BaseModel):
    parent_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=64)
    leader: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    sort: int | None = None
    status: Status | None = None
    remark: str | None = Field(default=None, max_length=500)


class ProfileUpdate(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    avatar_file_id: int | None = Field(default=None, ge=1)
