from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import AppError

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
GENERATED_ROOT = Path(__file__).resolve().parents[2] / "generated"

environment = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=StrictUndefined,
    autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=False),
    trim_blocks=True,
    lstrip_blocks=True,
)

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MODULE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SYSTEM_PREFIXES = ("sys_", "alembic_", "sqlite_", "_")


@dataclass
class ColumnSpec:
    name: str
    sa_type: str
    py_type: str
    nullable: bool
    primary_key: bool
    autoincrement: bool
    default: str | None = None
    length: int | None = None


@dataclass
class TableSpec:
    name: str
    columns: list[ColumnSpec] = field(default_factory=list)

    @property
    def primary_key(self) -> ColumnSpec:
        for column in self.columns:
            if column.primary_key:
                return column
        raise AppError("CODEGEN_NO_PRIMARY_KEY", "表没有主键，无法生成 CRUD", 422)

    @property
    def writable_columns(self) -> list[ColumnSpec]:
        skip = {"created_at", "updated_at"}
        return [
            column
            for column in self.columns
            if not column.primary_key and not column.autoincrement and column.name not in skip
        ]


def _python_type(column: Any) -> tuple[str, str]:
    from sqlalchemy import (
        JSON,
        BigInteger,
        Boolean,
        Date,
        DateTime,
        Float,
        Integer,
        LargeBinary,
        Numeric,
        SmallInteger,
        String,
        Text,
        Time,
    )

    column_type = column["type"]
    cls = type(column_type)
    length = getattr(column_type, "length", None)
    if isinstance(column_type, Boolean):
        return "Boolean", "bool"
    if isinstance(column_type, SmallInteger):
        return "SmallInteger", "int"
    if isinstance(column_type, BigInteger):
        return "BigInteger", "int"
    if isinstance(column_type, Integer):
        return "Integer", "int"
    if isinstance(column_type, (Numeric, Float)):
        if isinstance(column_type, Numeric):
            precision = getattr(column_type, "precision", None)
            scale = getattr(column_type, "scale", None)
            sa = f"Numeric({precision}, {scale})" if precision else "Numeric"
            return sa, "Decimal"
        return "Float", "float"
    if isinstance(column_type, DateTime):
        return "DateTime", "datetime"
    if isinstance(column_type, Date):
        return "Date", "date"
    if isinstance(column_type, Time):
        return "Time", "time"
    if isinstance(column_type, JSON):
        return "JSON", "dict[str, Any]"
    if isinstance(column_type, LargeBinary):
        return "LargeBinary", "bytes"
    if isinstance(column_type, Text):
        return "Text", "str"
    if isinstance(column_type, String):
        return (f"String({length})" if length else "String(255)"), "str"
    if cls.__name__ == "BOOLEAN":
        return "Boolean", "bool"
    return "Text", "str"


class CodeGenerator:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def _inspect(self, table: str | None = None) -> dict[str, Any]:
        def run(sync_connection):  # type: ignore[no-untyped-def]
            inspector = inspect(sync_connection)
            names = inspector.get_table_names()
            if table is None:
                return {"tables": names, "columns": {}}
            if table not in names:
                raise AppError("CODEGEN_TABLE_NOT_FOUND", "数据表不存在", 404)
            return {"tables": names, "columns": inspector.get_columns(table), "pk": inspector.get_pk_constraint(table)}

        async with self.engine.connect() as connection:
            return await connection.run_sync(run)

    async def list_tables(self) -> list[dict[str, Any]]:
        data = await self._inspect()
        result = []
        for name in sorted(data["tables"]):
            column_names: list[str] = []
            composite = True
            if IDENTIFIER.match(name):
                table_data = await self._inspect(name)
                column_names = [column["name"] for column in table_data["columns"]]
                composite = len(table_data["pk"].get("constrained_columns") or []) > 1
            system = name.startswith(SYSTEM_PREFIXES)
            result.append(
                {
                    "table": name,
                    "is_system": system,
                    "codegen_enabled": not system and not composite,
                    "column_count": len(column_names),
                    "columns": column_names,
                }
            )
        return result

    async def _columns(self, table: str) -> list[ColumnSpec]:
        if not IDENTIFIER.match(table):
            raise AppError("CODEGEN_TABLE_INVALID", "表名不合法", 422)
        data = await self._inspect(table)
        pk_columns = set(data["pk"].get("constrained_columns") or [])
        if len(pk_columns) > 1:
            raise AppError("CODEGEN_COMPOSITE_KEY", "首版代码生成不支持联合主键", 422)
        columns = []
        for column in data["columns"]:
            sa_type, py_type = _python_type(column)
            default = column.get("default")
            autoincrement = bool(column.get("autoincrement")) and column["name"] in pk_columns
            columns.append(
                ColumnSpec(
                    name=column["name"],
                    sa_type=sa_type,
                    py_type=py_type,
                    nullable=bool(column.get("nullable", True)),
                    primary_key=column["name"] in pk_columns,
                    autoincrement=autoincrement,
                    default=str(default) if default is not None else None,
                    length=getattr(column["type"], "length", None),
                )
            )
        return columns

    async def table_spec(self, table: str) -> TableSpec:
        if not IDENTIFIER.match(table):
            raise AppError("CODEGEN_TABLE_INVALID", "表名不合法", 422)
        if table.startswith(SYSTEM_PREFIXES):
            raise AppError("CODEGEN_TABLE_SYSTEM", "系统表不允许生成业务代码", 422)
        return TableSpec(name=table, columns=await self._columns(table))

    async def table_columns(self, table: str) -> list[dict[str, Any]]:
        columns = await self._columns(table)
        return [
            {
                "name": item.name,
                "type": item.sa_type,
                "nullable": item.nullable,
                "primary_key": item.primary_key,
                "autoincrement": item.autoincrement,
                "default": item.default,
            }
            for item in columns
        ]

    async def generate(
        self,
        *,
        table: str,
        module: str,
        resource: str | None = None,
        title: str | None = None,
    ) -> dict[str, str]:
        spec = await self.table_spec(table)
        if not MODULE_PATTERN.match(module):
            raise AppError("CODEGEN_MODULE_INVALID", "模块名必须为小写字母、数字或下划线", 422)
        resource = resource or table
        if not MODULE_PATTERN.match(resource):
            raise AppError("CODEGEN_RESOURCE_INVALID", "资源名必须为小写字母、数字或下划线", 422)
        class_name = "".join(part.capitalize() for part in resource.split("_")) or "Generated"
        pk = spec.primary_key
        context = {
            "table": spec.name,
            "module": module,
            "resource": resource,
            "class_name": class_name,
            "title": title or spec.name,
            "columns": spec.columns,
            "writable_columns": spec.writable_columns,
            "pk": pk,
            "permission_prefix": f"{module}:{resource}",
            "route_prefix": f"/{module}/{resource}",
            "sa_imports": sorted({item.sa_type.split("(")[0] for item in spec.columns}),
            "py_imports": sorted(
                {
                    item.py_type.replace("dict[str, Any]", "").replace("list[", "").replace("]", "").strip()
                    for item in spec.columns
                }
                - {""}
            ),
        }
        files: dict[str, str] = {}
        templates = {
            "model.py": "model.py.j2",
            "schemas.py": "schemas.py.j2",
            "service.py": "service.py.j2",
            "router.py": "router.py.j2",
            "frontend/page.html": "frontend.page.html.j2",
            "frontend/index.js": "frontend.page.js.j2",
            "README.md": "README.md.j2",
        }
        for relative, template_name in templates.items():
            template = environment.get_template(template_name)
            content = template.render(**context).strip() + "\n"
            files[relative] = content
        manifest = {
            "table": spec.name,
            "module": module,
            "resource": resource,
            "class_name": class_name,
            "files": {
                path: hashlib.sha256(content.encode("utf-8")).hexdigest()
                for path, content in sorted(files.items())
            },
        }
        files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        return files

    @staticmethod
    def write_files(files: dict[str, str], destination: Path) -> list[str]:
        root = destination.resolve()
        if not root.is_relative_to(GENERATED_ROOT.resolve()):
            raise AppError("CODEGEN_PATH_INVALID", "输出目录必须位于 generated/ 下", 422)
        if root.exists() and any(root.iterdir()):
            raise AppError("CODEGEN_TARGET_EXISTS", "目标目录已存在且非空，不覆盖已有文件", 409)
        written = []
        for relative, content in files.items():
            target = (root / relative).resolve()
            if not target.is_relative_to(root):
                raise AppError("CODEGEN_PATH_INVALID", "生成文件路径非法", 400)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise AppError("CODEGEN_FILE_EXISTS", f"文件已存在：{relative}", 409)
            target.write_text(content, encoding="utf-8")
            written.append(str(target))
        return written

    @staticmethod
    def zip_bytes(files: dict[str, str], prefix: str = "") -> bytes:
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, content in files.items():
                archive.writestr(f"{prefix}/{path}" if prefix else path, content)
        return buffer.getvalue()
