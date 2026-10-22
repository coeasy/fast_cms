from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    table: str = Field(min_length=1, max_length=64)
    module: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    resource: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    title: str | None = Field(default=None, max_length=64)
