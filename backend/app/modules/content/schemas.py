from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.envelope import StrictModel


class NoticeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=50000)
    status: Literal["draft", "published"] = "draft"
    is_top: bool = False


class NoticeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    content: str | None = Field(default=None, max_length=50000)
    is_top: bool | None = None


class NoticeOut(StrictModel):
    """``notice_out()`` 的响应契约；``read`` 只在用户端列表/详情出现。"""

    id: int
    title: str
    content: str
    status: str
    is_top: bool
    published_at: str | None = None
    created_by: int | None = None
    updated_by: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    read: bool | None = None
