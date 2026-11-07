from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.envelope import StrictModel
from app.kernel.schemas import SessionOut, UserOut
from app.modules.sys.system.schemas import MenuOut


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class TokenOut(StrictModel):
    """改密/登录后的访问令牌；刷新令牌只走 HttpOnly Cookie，不出现在响应体里。"""

    access_token: str
    token_type: str
    expires_in: int
    user: UserOut


class LoginOut(TokenOut):
    must_change_password: bool


class MeOut(StrictModel):
    user: UserOut
    permissions: list[str]
    is_superuser: bool
    is_admin: bool
    menus: list[MenuOut]
    # API Key 身份没有会话，返回 null 让前端能区分两种身份
    session: SessionOut | None = None
    via_api_key: bool


class HeartbeatOut(StrictModel):
    server_time: str


class RevokedOut(StrictModel):
    revoked: int
