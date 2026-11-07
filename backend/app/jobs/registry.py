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


class EventOutboxDrainArgs(BaseModel):
    limit: int = Field(default=100, ge=1, le=500, description="单次最多派发多少条待投递事件")


@dataclass(frozen=True)
class JobDefinition[ArgsT: BaseModel]:
    code: str
    name: str
    description: str
    args_model: type[ArgsT]
    handler: Callable[[ArgsT], Awaitable[str | None]]
    # 高频 interval 任务（如通知投递，每 60 秒一轮）在「本轮无事可做」时返回 None，
    # 并配合这个标记把本次执行日志删掉：否则一天 1440 条空日志会把任务日志页面刷满，
    # 真正需要排查的失败记录反而被淹没。非空闲轮次照常留日志。
    quiet_when_idle: bool = False


REGISTRY: dict[str, JobDefinition] = {}


def register(definition: JobDefinition) -> None:
    REGISTRY[definition.code] = definition


def get_definition(code: str) -> JobDefinition:
    definition = REGISTRY.get(code)
    if definition is None:
        raise AppError("SCHEDULE_JOB_UNKNOWN", status_code=422)
    return definition


def is_quiet_when_idle(code: str) -> bool:
    """任务是否声明「空闲轮次不留日志」。未注册的代码按 False 处理，不抛异常。"""
    definition = REGISTRY.get(code)
    return bool(definition is not None and definition.quiet_when_idle)


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
        raise AppError("SCHEDULE_JOB_ARGS_INVALID", status_code=422, details=exc.errors()) from exc


async def execute_job(code: str, args: dict[str, Any]) -> str | None:
    definition = get_definition(code)
    validated = definition.args_model.model_validate(args or {})
    return await definition.handler(validated)


def _load_builtins() -> None:
    from app.jobs import builtin

    builtin.register_builtins()


_load_builtins()
