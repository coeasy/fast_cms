"""内核级响应模型：与 :mod:`app.kernel.serializers` 的 ``*_out`` 一一对应。

这些结构被多个模块复用（auth / system / monitor / export），因此和序列化函数
放在同一层，避免每个模块各写一份副本导致契约漂移。
"""

from __future__ import annotations

from app.core.envelope import StrictModel


class RoleBrief(StrictModel):
    id: int
    name: str
    code: str


class UserBrief(StrictModel):
    id: int
    username: str
    nickname: str
    dept_id: int | None = None
    dept_name: str | None = None
    status: str
    is_superuser: bool


class UserOut(UserBrief):
    email: str | None = None
    phone: str | None = None
    avatar_file_id: int | None = None
    must_change_password: bool
    locked_until: str | None = None
    last_login_at: str | None = None
    last_login_ip: str | None = None
    last_login_location: str | None = None
    remark: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    roles: list[RoleBrief] | None = None


class SessionOut(StrictModel):
    id: str
    ip: str | None = None
    user_agent: str | None = None
    last_seen_at: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    current: bool


class OnlineSessionOut(SessionOut):
    """在线会话列表的一行：``id`` 是**会话 id**（强制下线按它定位），用户 id 在 ``user_id``。"""

    user_id: int
    username: str
    nickname: str
    dept_id: int | None = None
    dept_name: str | None = None
    status: str
    is_superuser: bool
    location: str | None = None


class FileOut(StrictModel):
    id: int
    original_name: str
    content_type: str | None = None
    size: int
    sha256: str | None = None
    uploader_id: int | None = None
    uploader_name: str | None = None
    created_at: str | None = None
    download_url: str
