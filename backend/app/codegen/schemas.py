from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.envelope import StrictModel


class GenerateRequest(BaseModel):
    table: str = Field(min_length=1, max_length=64)
    module: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    resource: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    title: str | None = Field(default=None, max_length=64)
    # A-2：生成产物的落地域分组（modules/<域>/<module>/）。缺省按资源类型推断。
    domain: str | None = Field(default=None, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")


class CodegenTableOut(StrictModel):
    """可生成的数据表：系统表与复合主键表标为不可生成，避免生成出跑不起来的模块。"""

    table: str
    is_system: bool
    codegen_enabled: bool
    column_count: int
    columns: list[str]


class CodegenColumnOut(StrictModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool
    autoincrement: bool
    default: str | None = None


class GeneratedFileOut(StrictModel):
    path: str
    content: str


class CodegenPreviewOut(StrictModel):
    files: list[GeneratedFileOut]


class CodegenWriteOut(StrictModel):
    written: list[str]
