from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
