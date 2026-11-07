from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.envelope import StrictModel
from app.modules.ops.notify.models import CHANNEL_TYPES, SUBSCRIBABLE_EVENTS

ChannelType = Literal["dingtalk", "feishu", "wecom", "webhook"]


def _check_types(values: list[str]) -> list[str]:
    unknown = [value for value in values if value not in SUBSCRIBABLE_EVENTS]
    if unknown:
        raise ValueError(f"不支持的订阅事件：{', '.join(unknown)}")
    return sorted(set(values))


class NotifyChannelBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    channel_type: ChannelType
    secret: str | None = Field(default=None, max_length=255)
    events: list[str] = Field(default_factory=lambda: ["job.failed"], min_length=1)
    status: Literal["enabled", "disabled"] = "enabled"
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("events")
    @classmethod
    def _validate_events(cls, values: list[str]) -> list[str]:
        return _check_types(values)

    @field_validator("channel_type")
    @classmethod
    def _validate_channel_type(cls, value: str) -> str:
        if value not in CHANNEL_TYPES:
            raise ValueError(f"不支持的渠道类型：{value}")
        return value


class NotifyChannelCreate(NotifyChannelBase):
    webhook_url: str = Field(min_length=8, max_length=1000)


class NotifyChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    channel_type: ChannelType | None = None
    # 留空表示保留原有地址与密钥，避免脱敏回显后再提交导致凭证丢失。
    webhook_url: str | None = Field(default=None, min_length=8, max_length=1000)
    secret: str | None = Field(default=None, max_length=255)
    events: list[str] | None = Field(default=None, min_length=1)
    status: Literal["enabled", "disabled"] | None = None
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("events")
    @classmethod
    def _validate_events(cls, values: list[str] | None) -> list[str] | None:
        return _check_types(values) if values is not None else None


class ChannelOut(StrictModel):
    id: int
    name: str
    channel_type: str
    # 只回显掩码：webhook 里常带 token，明文回显等于把凭证写进浏览器缓存
    webhook_url_masked: str
    has_secret: bool
    events: list[str]
    status: str
    remark: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class RecordOut(StrictModel):
    id: int
    channel_id: int | None = None
    channel_name: str
    channel_type: str
    event: str
    dedupe_key: str
    title: str
    status: str
    attempts: int
    last_error: str | None = None
    response_status: int | None = None
    sent_at: str | None = None
    next_retry_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # 详情与「测试渠道/重投」才带正文，列表不带（正文可能很大）
    content: str | None = None
