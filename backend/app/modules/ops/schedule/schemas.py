from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.envelope import StrictModel

TriggerType = Literal["cron", "interval", "date"]
TemplateTriggerType = Literal["cron", "interval"]
JobStatus = Literal["enabled", "paused"]
JobType = Literal["registry", "python"]

SCRIPT_MAX_LENGTH = 200_000
JOB_CODE_PATTERN = r"^[a-z][a-z0-9_.]*$"
TEMPLATE_CODE_PATTERN = r"^[a-z][a-z0-9_.-]*$"


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=3, max_length=128, pattern=JOB_CODE_PATTERN)
    description: str | None = Field(default=None, max_length=255)
    job_type: JobType | None = None
    script_code: str | None = Field(default=None, max_length=SCRIPT_MAX_LENGTH)
    template_id: int | None = Field(default=None, ge=1)
    trigger_type: TriggerType | None = None
    cron_expr: str | None = Field(default=None, max_length=128)
    interval_seconds: int | None = Field(default=None, ge=10, le=86400 * 30)
    run_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    args_json: dict[str, Any] | None = None
    status: JobStatus = "enabled"
    timeout_seconds: int | None = Field(default=None, ge=5, le=3600)


class JobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    script_code: str | None = Field(default=None, max_length=SCRIPT_MAX_LENGTH)
    trigger_type: TriggerType | None = None
    cron_expr: str | None = Field(default=None, max_length=128)
    interval_seconds: int | None = Field(default=None, ge=10, le=86400 * 30)
    run_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    args_json: dict[str, Any] | None = None
    status: JobStatus | None = None
    timeout_seconds: int | None = Field(default=None, ge=5, le=3600)


class PythonScriptPayload(BaseModel):
    script_code: str = Field(min_length=1, max_length=SCRIPT_MAX_LENGTH)


class JobTemplateCreate(BaseModel):
    code: str = Field(min_length=3, max_length=128, pattern=TEMPLATE_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    job_type: JobType = "registry"
    handler_code: str | None = Field(default=None, max_length=128, pattern=JOB_CODE_PATTERN)
    script_code: str | None = Field(default=None, max_length=SCRIPT_MAX_LENGTH)
    args_json: dict[str, Any] = Field(default_factory=dict)
    trigger_type: TemplateTriggerType = "cron"
    cron_expr: str | None = Field(default=None, max_length=128)
    interval_seconds: int | None = Field(default=None, ge=10, le=86400 * 30)
    timezone: str | None = Field(default=None, max_length=64)
    timeout_seconds: int = Field(default=300, ge=5, le=3600)


class JobTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    job_type: JobType | None = None
    handler_code: str | None = Field(default=None, max_length=128, pattern=JOB_CODE_PATTERN)
    script_code: str | None = Field(default=None, max_length=SCRIPT_MAX_LENGTH)
    args_json: dict[str, Any] | None = None
    trigger_type: TemplateTriggerType | None = None
    cron_expr: str | None = Field(default=None, max_length=128)
    interval_seconds: int | None = Field(default=None, ge=10, le=86400 * 30)
    timezone: str | None = Field(default=None, max_length=64)
    timeout_seconds: int | None = Field(default=None, ge=5, le=3600)


class JobOut(StrictModel):
    id: int
    name: str
    code: str
    job_type: str
    template_id: int | None = None
    description: str | None = None
    trigger_type: str
    cron_expr: str | None = None
    interval_seconds: int | None = None
    run_at: str | None = None
    timezone: str
    args_json: dict[str, Any]
    status: str
    timeout_seconds: int
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    is_builtin: bool
    created_by: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # 仅在具备 schedule:job:code 且查详情时返回，列表一律不带
    script_code: str | None = None


class JobTemplateOut(StrictModel):
    id: int
    code: str
    name: str
    description: str | None = None
    job_type: str
    handler_code: str | None = None
    script_code: str | None = None
    args_json: dict[str, Any]
    trigger_type: str
    cron_expr: str | None = None
    interval_seconds: int | None = None
    timezone: str
    timeout_seconds: int
    is_builtin: bool
    created_by: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class JobLogOut(StrictModel):
    id: int
    job_id: int | None = None
    job_name: str
    job_code: str
    job_type: str
    trigger_type: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    created_at: str | None = None
    # 详情才带：输出与错误可能很长
    output: str | None = None
    error: str | None = None
    code_hash: str | None = None


class JobDefinitionOut(StrictModel):
    code: str
    name: str
    description: str
    # 注册表里参数模型的 JSON Schema，前端据此渲染表单
    args_schema: dict[str, Any]


class ScheduleCapabilities(BaseModel):
    python_enabled: bool
    can_manage_python: bool


class ScriptValidateOut(StrictModel):
    valid: bool


class TriggerAccepted(BaseModel):
    """手动触发是异步投递：``accepted`` 表示任务已入队，不代表已执行成功。"""

    accepted: bool
    job_id: int
