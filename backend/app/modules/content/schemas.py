from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NoticeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=50000)
    status: Literal["draft", "published"] = "draft"
    is_top: bool = False


class NoticeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    content: str | None = Field(default=None, max_length=50000)
    is_top: bool | None = None
