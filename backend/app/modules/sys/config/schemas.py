from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.envelope import StrictModel

Status = Literal["active", "disabled"]
ConfigType = Literal["string", "int", "bool", "json"]


class DictTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")
    status: Status = "active"
    remark: str | None = Field(default=None, max_length=255)


class DictTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    status: Status | None = None
    remark: str | None = Field(default=None, max_length=255)


class DictItemCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=64)
    tag_color: str | None = Field(default=None, max_length=32)
    sort: int = 0
    status: Status = "active"
    remark: str | None = Field(default=None, max_length=255)


class DictItemUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    value: str | None = Field(default=None, min_length=1, max_length=64)
    tag_color: str | None = Field(default=None, max_length=32)
    sort: int | None = None
    status: Status | None = None
    remark: str | None = Field(default=None, max_length=255)


class ConfigCreate(BaseModel):
    key: str = Field(min_length=2, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    name: str = Field(min_length=1, max_length=128)
    value: str = ""
    type: ConfigType = "string"
    group: str = Field(default="basic", max_length=64)
    is_public: bool = False
    remark: str | None = Field(default=None, max_length=255)


class ConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    value: str | None = None
    type: ConfigType | None = None
    group: str | None = Field(default=None, max_length=64)
    is_public: bool | None = None
    remark: str | None = Field(default=None, max_length=255)


class DictItemOut(StrictModel):
    id: int
    dict_type_id: int
    label: str
    value: str
    tag_color: str | None = None
    sort: int
    status: str
    remark: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DictTypeOut(StrictModel):
    id: int
    name: str
    code: str
    status: str
    remark: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # 列表给计数、详情给完整条目，两者不同时出现
    item_count: int | None = None
    items: list[DictItemOut] | None = None


class ConfigOut(StrictModel):
    id: int
    key: str
    name: str
    value: str
    type: str
    group: str
    is_public: bool
    built_in: bool
    remark: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DictEntryOut(StrictModel):
    """前台读字典：只给展示需要的三列。"""

    label: str
    value: str
    tag_color: str | None = None


class DictReadOut(StrictModel):
    code: str
    name: str
    items: list[DictEntryOut]
