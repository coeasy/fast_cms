from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
