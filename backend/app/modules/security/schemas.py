from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.ipguard import normalize_kind, normalize_scope, normalize_target


class IpRuleCreate(BaseModel):
    target: str = Field(max_length=64)
    kind: str = "block"
    scope: str = "all"
    reason: str | None = Field(default=None, max_length=255)
    # None=永久；否则为到期时间，到期后中间件自动忽略并回写状态
    expires_at: datetime | None = None

    @field_validator("target")
    @classmethod
    def _canonical_target(cls, value: str) -> str:
        try:
            return normalize_target(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        try:
            return normalize_kind(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value: str) -> str:
        try:
            return normalize_scope(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class IpRuleUpdate(BaseModel):
    reason: str | None = Field(default=None, max_length=255)
    scope: str | None = None
    expires_at: datetime | None = None
    status: str | None = None

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_scope(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("active", "disabled"):
            raise ValueError("状态只能是 active / disabled")
        return value


class IpRuleBlockNow(BaseModel):
    """从登录日志一键封禁：允许直接传 IP，也允许由前端先解析好的网段。"""

    ip: str = Field(max_length=64)
    scope: str = "all"
    reason: str | None = Field(default=None, max_length=255)
    # 临时封禁时长（分钟）；0 或不传表示永久
    minutes: int = Field(default=0, ge=0, le=60 * 24 * 365)

    @field_validator("ip")
    @classmethod
    def _canonical_ip(cls, value: str) -> str:
        try:
            return normalize_target(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value: str) -> str:
        try:
            return normalize_scope(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class UserBanCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=255)
    # None=永久封禁；否则为解封时间
    until: datetime | None = None
    # 是否同时把该账号最近使用的 IP 加入封禁网段
    ban_ip: bool = True
    ip_minutes: int = Field(default=0, ge=0, le=60 * 24 * 365)


class ApiKeyCreate(BaseModel):
    name: str = Field(max_length=64)
    scopes: str = Field(default="", max_length=2000)
    expires_at: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)
