from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.envelope import StrictModel
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


class IpRuleOut(StrictModel):
    id: int
    kind: str
    # target 是展示形态（网段会带 /24 等），canonical_target 才是入库的原值
    target: str
    canonical_target: str
    scope: str
    reason: str | None = None
    status: str
    auto: bool
    expires_at: str | None = None
    created_by_name: str | None = None
    created_at: str | None = None
    hit_count: int
    last_hit_at: str | None = None


class MatchedRuleOut(StrictModel):
    """命中规则的展示形态：字段比 ``IpRuleOut`` 少，没有状态与创建人。"""

    id: int
    kind: str
    target: str
    canonical_target: str
    scope: str
    reason: str | None = None
    expires_at: str | None = None
    hit_count: int
    last_hit_at: str | None = None


class IpLocationOut(StrictModel):
    country: str | None = None
    province: str | None = None
    city: str | None = None
    isp: str | None = None
    country_code: str | None = None
    label: str
    place: str
    domestic: bool
    raw: str


class IpLibraryVersionOut(StrictModel):
    version: str
    path: str
    size_bytes: int
    loaded: bool


class IpLibraryOut(StrictModel):
    """归属地库摘要：只输出路径/大小/是否加载，**不含**内部 searcher（带 bytes，编码即失败）。"""

    enabled: bool
    policy: str
    data_dir: str
    versions: list[IpLibraryVersionOut]


class ScopeVerdict(BaseModel):
    blocked: bool
    rule: MatchedRuleOut | None = None


class IpLookupOut(StrictModel):
    ip: str
    canonical: str
    blocked: bool
    scopes: dict[str, ScopeVerdict]


class IpLocateOut(StrictModel):
    ip: str
    canonical: str
    location: IpLocationOut | None = None
    rules: IpLookupOut
    library: IpLibraryOut


class BannedUserOut(StrictModel):
    id: int
    username: str
    nickname: str
    dept_name: str | None = None
    status: str
    last_login_ip: str | None = None
    last_login_location: str | None = None
    banned: bool
    banned_until: str | None = None
    ban_reason: str | None = None
    banned_at: str | None = None


class BanResultOut(StrictModel):
    user_id: int
    # 封禁时连带生成的 IP 规则；不连带封 IP 时为 None
    ip_rule_id: int | None = None
    banned: bool
    banned_until: str | None = None
    ban_reason: str | None = None
    banned_at: str | None = None


class UnbanResultOut(StrictModel):
    user_id: int
    banned: bool
    banned_until: str | None = None
    ban_reason: str | None = None
    banned_at: str | None = None


class ApiKeyOut(StrictModel):
    id: int
    name: str
    key_prefix: str
    scopes: list[str]
    status: str
    expires_at: str | None = None
    last_used_at: str | None = None
    last_used_ip: str | None = None
    remark: str | None = None
    created_by_name: str | None = None
    created_at: str | None = None


class ApiKeyCreatedOut(ApiKeyOut):
    """创建时唯一一次返回明文 ``key``，之后只能看到前缀。"""

    key: str
