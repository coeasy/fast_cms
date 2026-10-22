from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.core.errors import AppError


class CleanupSessionsArgs(BaseModel):
    days: int = Field(default=30, ge=1, le=3650, description="清理早于多少天前过期的会话")


class CleanupLogsArgs(BaseModel):
    days: int = Field(default=90, ge=7, le=3650, description="保留天数")


class CleanupFilesArgs(BaseModel):
    days: int = Field(default=30, ge=1, le=3650, description="仅清理早于此天数的未引用文件")


class HeartbeatArgs(BaseModel):
    message: str = Field(default="heartbeat", max_length=200)


class NotifyDispatchArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="单次最多投递多少条待发送记录")


@dataclass(frozen=True)
class JobDefinition[ArgsT: BaseModel]:
    code: str
    name: str
    description: str
    args_model: type[ArgsT]
    handler: Callable[[ArgsT], Awaitable[str | None]]


REGISTRY: dict[str, JobDefinition] = {}


def register(definition: JobDefinition) -> None:
    REGISTRY[definition.code] = definition


def get_definition(code: str) -> JobDefinition:
    definition = REGISTRY.get(code)
    if definition is None:
        raise AppError("SCHEDULE_JOB_UNKNOWN", "任务代码未在注册表中定义", 422)
    return definition


def list_definitions() -> list[dict[str, Any]]:
    return [
        {
            "code": item.code,
            "name": item.name,
            "description": item.description,
            "args_schema": item.args_model.model_json_schema(),
        }
        for item in sorted(REGISTRY.values(), key=lambda entry: entry.code)
    ]


def validate_args(code: str, args: dict[str, Any]) -> dict[str, Any]:
    definition = get_definition(code)
    try:
        return definition.args_model.model_validate(args or {}).model_dump()
    except ValidationError as exc:
        raise AppError("SCHEDULE_JOB_ARGS_INVALID", "任务参数不合法", 422, exc.errors()) from exc


async def execute_job(code: str, args: dict[str, Any]) -> str | None:
    definition = get_definition(code)
    validated = definition.args_model.model_validate(args or {})
    return await definition.handler(validated)


def _load_builtins() -> None:
    from app.jobs import builtin  # noqa: F401  (registers handlers on import)

    builtin.register_builtins()


_load_builtins()
