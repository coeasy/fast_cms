from __future__ import annotations

import asyncio
import logging
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext
from app.core.config import settings
from app.core.errors import AppError
from app.core.pagination import PageParams, paginate
from app.core.search import LIKE_ESCAPE, contains
from app.core.serializers import iso
from app.core.tz import scheduler_timezone
from app.db.base import utcnow
from app.jobs.python_runner import validate_python_script
from app.jobs.registry import get_definition, list_definitions, validate_args
from app.jobs.runner import log_out, run_job
from app.kernel.db import commit
from app.modules.ops.schedule.models import Job, JobLog, JobTemplate
from app.modules.ops.schedule.scheduler import (
    api_owns_next_run,
    build_trigger,
    compute_next_run,
    preview_next_runs,
    scheduler_service,
)
from app.modules.ops.schedule.schemas import (
    JobCreate,
    JobTemplateCreate,
    JobTemplateUpdate,
    JobUpdate,
)

logger = logging.getLogger("fastcms.schedule")

_background_tasks: set[asyncio.Task[Any]] = set()


def _consume_task(task: asyncio.Task[Any]) -> None:
    """手动触发的后台任务收尾：移除强引用并取出异常。

    只 discard 不取 ``exception()`` 的话，任务里抛的异常永远不会被读走，
    只在 GC 时冒一条 "Task exception was never retrieved" —— 生产环境里
    等于「点了立即执行，什么也没发生，也没有任何线索」。
    """
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("手动触发任务执行失败", exc_info=exc)

# 间隔任务的最小秒数：低于这个值等于「持续空转」，会把调度器与数据库打满。
# 校验与错误文案（MESSAGES 的 {seconds} 占位符）共用这一处。
MIN_INTERVAL_SECONDS = 10

TRIGGER_FIELDS = (
    "trigger_type",
    "cron_expr",
    "interval_seconds",
    "run_at",
    "timezone",
    "status",
)


def job_out(job: Job, *, include_script: bool = False) -> dict[str, Any]:
    data = {
        "id": job.id,
        "name": job.name,
        "code": job.code,
        "job_type": job.job_type or "registry",
        "template_id": job.template_id,
        "description": job.description,
        "trigger_type": job.trigger_type,
        "cron_expr": job.cron_expr,
        "interval_seconds": job.interval_seconds,
        "run_at": iso(job.run_at),
        "timezone": job.timezone,
        "args_json": job.args_json or {},
        "status": job.status,
        "timeout_seconds": job.timeout_seconds,
        "next_run_at": iso(job.next_run_at),
        "last_run_at": iso(job.last_run_at),
        "last_status": job.last_status,
        "is_builtin": job.is_builtin,
        "created_by": job.created_by,
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
    }
    if include_script:
        data["script_code"] = job.script_code
    return data


def template_out(item: JobTemplate) -> dict[str, Any]:
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "job_type": item.job_type,
        "handler_code": item.handler_code,
        "script_code": item.script_code,
        "args_json": item.args_json or {},
        "trigger_type": item.trigger_type,
        "cron_expr": item.cron_expr,
        "interval_seconds": item.interval_seconds,
        "timezone": item.timezone,
        "timeout_seconds": item.timeout_seconds,
        "is_builtin": item.is_builtin,
        "created_by": item.created_by,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


def _require_python_permission(ctx: AuthContext) -> None:
    if not ctx.has("schedule:job:code"):
        raise AppError(
            "SCHEDULE_JOB_CODE_FORBIDDEN",
            status_code=403,
        )


def _require_python_enabled() -> None:
    if not settings.scheduler_python_enabled:
        raise AppError(
            "SCHEDULE_PYTHON_DISABLED",
            status_code=403,
        )


def _validate_trigger_fields(
    *,
    trigger_type: str,
    cron_expr: str | None,
    interval_seconds: int | None,
    run_at: Any,
    timezone: str,
    require_future: bool = True,
) -> None:
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise AppError("SCHEDULE_TIMEZONE_INVALID", status_code=422) from exc
    if trigger_type == "cron":
        if not cron_expr:
            raise AppError("SCHEDULE_CRON_REQUIRED", status_code=422)
        from apscheduler.triggers.cron import CronTrigger

        try:
            CronTrigger.from_crontab(cron_expr.strip(), timezone=ZoneInfo(timezone))
        except Exception as exc:
            raise AppError("SCHEDULE_CRON_INVALID", status_code=422) from exc
    elif trigger_type == "interval":
        if not interval_seconds or interval_seconds < MIN_INTERVAL_SECONDS:
            raise AppError(
                "SCHEDULE_INTERVAL_INVALID",
                status_code=422,
                params={"seconds": MIN_INTERVAL_SECONDS},
            )
    elif trigger_type == "date":
        if run_at is None:
            raise AppError("SCHEDULE_DATE_REQUIRED", status_code=422)
        if run_at.tzinfo is None:
            raise AppError("SCHEDULE_DATE_TIMEZONE_REQUIRED", status_code=422)
        if require_future and run_at <= utcnow():
            raise AppError("SCHEDULE_DATE_PAST", status_code=422)
    else:
        raise AppError("SCHEDULE_TRIGGER_INVALID", status_code=422)


def _validate_job_definition(
    *,
    job_type: str,
    code: str,
    script_code: str | None,
    args: dict[str, Any],
    ctx: AuthContext,
    require_python_enabled: bool,
) -> None:
    if job_type == "registry":
        get_definition(code)
        validate_args(code, args)
        return
    if job_type != "python":
        raise AppError("SCHEDULE_JOB_TYPE_INVALID", status_code=422)
    _require_python_permission(ctx)
    validate_python_script(script_code)
    if require_python_enabled:
        _require_python_enabled()


def _normalize_trigger(job: Job) -> None:
    if job.trigger_type == "cron":
        job.interval_seconds = None
        job.run_at = None
    elif job.trigger_type == "interval":
        job.cron_expr = None
        job.run_at = None
    elif job.trigger_type == "date":
        job.cron_expr = None
        job.interval_seconds = None


async def list_jobs(
    db: AsyncSession,
    params: PageParams,
    *,
    keyword: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    statement = select(Job)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(Job.name).like(like, escape=LIKE_ESCAPE),
                Job.code.like(like, escape=LIKE_ESCAPE),
            )
        )
    if status:
        statement = statement.where(Job.status == status)
    if job_type:
        statement = statement.where(Job.job_type == job_type)
    statement = statement.order_by(Job.id.desc())
    rows, total = await paginate(db, statement, params)
    return [job_out(item) for item in rows], total


async def get_job(
    db: AsyncSession, job_id: int, *, include_script: bool = False
) -> dict[str, Any]:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("SCHEDULE_JOB_NOT_FOUND", status_code=404)
    return job_out(job, include_script=include_script)


async def create_job(db: AsyncSession, ctx: AuthContext, data: JobCreate) -> dict[str, Any]:
    template: JobTemplate | None = None
    if data.template_id is not None:
        template = await db.get(JobTemplate, data.template_id)
        if template is None:
            raise AppError("SCHEDULE_TEMPLATE_NOT_FOUND", status_code=404)
        if data.job_type and data.job_type != template.job_type:
            raise AppError("SCHEDULE_TEMPLATE_TYPE_MISMATCH", status_code=422)
        if template.job_type == "registry" and template.handler_code and data.code != template.handler_code:
            raise AppError(
                "SCHEDULE_TEMPLATE_HANDLER_MISMATCH",
                status_code=422,
            )

    job_type = data.job_type or (template.job_type if template else "registry")
    script_code = data.script_code
    if script_code is None and template is not None:
        script_code = template.script_code
    args = data.args_json
    if args is None:
        args = dict(template.args_json or {}) if template is not None else {}
    trigger_type = data.trigger_type or (template.trigger_type if template else "cron")
    cron_expr = data.cron_expr if data.cron_expr is not None else (template.cron_expr if template else None)
    interval_seconds = (
        data.interval_seconds
        if data.interval_seconds is not None
        else (template.interval_seconds if template else None)
    )
    timezone = data.timezone or (template.timezone if template else None) or scheduler_timezone()
    timeout_seconds = (
        data.timeout_seconds
        if data.timeout_seconds is not None
        else (template.timeout_seconds if template else settings.scheduler_job_timeout)
    )

    if job_type == "python":
        exists = (
            await db.execute(select(Job.id).where(Job.code == data.code))
        ).scalar_one_or_none()
        if exists:
            raise AppError("SCHEDULE_JOB_CODE_EXISTS", status_code=409)

    _validate_trigger_fields(
        trigger_type=trigger_type,
        cron_expr=cron_expr,
        interval_seconds=interval_seconds,
        run_at=data.run_at,
        timezone=timezone,
    )
    _validate_job_definition(
        job_type=job_type,
        code=data.code,
        script_code=script_code,
        args=args,
        ctx=ctx,
        require_python_enabled=True,
    )

    job = Job(
        name=data.name,
        code=data.code,
        description=data.description,
        job_type=job_type,
        script_code=script_code if job_type == "python" else None,
        template_id=data.template_id,
        trigger_type=trigger_type,
        cron_expr=cron_expr,
        interval_seconds=interval_seconds,
        run_at=data.run_at,
        timezone=timezone,
        args_json=args,
        status=data.status,
        timeout_seconds=timeout_seconds,
        is_builtin=False,
        created_by=ctx.user.id,
    )
    _normalize_trigger(job)
    if job.status == "enabled" and api_owns_next_run():
        job.next_run_at = compute_next_run(job)
    db.add(job)
    await commit(db)
    await db.refresh(job)
    await scheduler_service.reload_job(job.id)
    return await get_job(db, job.id, include_script=True)


async def update_job(db: AsyncSession, ctx: AuthContext, job_id: int, data: JobUpdate) -> dict[str, Any]:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("SCHEDULE_JOB_NOT_FOUND", status_code=404)
    payload = data.model_dump(exclude_unset=True)
    if payload.get("args_json") is None:
        payload.pop("args_json", None)
    job_type = job.job_type or "registry"
    if job_type == "python":
        _require_python_permission(ctx)
        if payload.get("status") == "enabled" and not settings.scheduler_python_enabled:
            _require_python_enabled()

    merged = {
        "trigger_type": payload.get("trigger_type", job.trigger_type),
        "cron_expr": payload.get("cron_expr", job.cron_expr),
        "interval_seconds": payload.get("interval_seconds", job.interval_seconds),
        "run_at": payload.get("run_at", job.run_at),
        "timezone": payload.get("timezone", job.timezone),
        "args_json": payload.get("args_json", job.args_json or {}),
        "script_code": payload.get("script_code", job.script_code),
    }
    target_status = payload.get("status", job.status)
    _validate_trigger_fields(
        trigger_type=merged["trigger_type"],
        cron_expr=merged["cron_expr"],
        interval_seconds=merged["interval_seconds"],
        run_at=merged["run_at"],
        timezone=merged["timezone"],
        require_future=target_status == "enabled",
    )
    _validate_job_definition(
        job_type=job_type,
        code=job.code,
        script_code=merged["script_code"],
        args=merged["args_json"] or {},
        ctx=ctx,
        require_python_enabled=False,
    )

    for field, value in payload.items():
        setattr(job, field, value)
    job.script_code = merged["script_code"] if job_type == "python" else None
    schedule_changed = any(field in payload for field in TRIGGER_FIELDS)
    _normalize_trigger(job)
    if api_owns_next_run():
        # standalone 时写权在独立调度进程，API 只读，不写 next_run_at（A-9 / 3-6）
        if job.status == "paused":
            job.next_run_at = None
        elif schedule_changed:
            try:
                job.next_run_at = compute_next_run(job)
            except ValueError as exc:
                raise AppError("SCHEDULE_TRIGGER_INVALID", str(exc), 422) from exc
    await commit(db)
    await scheduler_service.reload_job(job.id)
    return await get_job(db, job.id, include_script=True)


async def delete_job(db: AsyncSession, job_id: int) -> None:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("SCHEDULE_JOB_NOT_FOUND", status_code=404)
    if job.is_builtin:
        raise AppError("SCHEDULE_JOB_BUILTIN", status_code=409)
    await scheduler_service.remove_job(job.id)
    await db.delete(job)
    await commit(db)


async def set_job_state(
    db: AsyncSession, ctx: AuthContext, job_id: int, status: str
) -> dict[str, Any]:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("SCHEDULE_JOB_NOT_FOUND", status_code=404)
    if status not in {"enabled", "paused"}:
        raise AppError("SCHEDULE_STATE_INVALID", status_code=422)
    if status == "enabled":
        if (job.job_type or "registry") == "python":
            _require_python_permission(ctx)
            _require_python_enabled()
        _validate_trigger_fields(
            trigger_type=job.trigger_type,
            cron_expr=job.cron_expr,
            interval_seconds=job.interval_seconds,
            run_at=job.run_at,
            timezone=job.timezone,
            require_future=True,
        )
    job.status = status
    if api_owns_next_run():
        # standalone 时写权在独立调度进程，API 只读，不写 next_run_at（A-9 / 3-6）
        if status == "enabled":
            try:
                job.next_run_at = compute_next_run(job)
            except ValueError as exc:
                raise AppError("SCHEDULE_TRIGGER_INVALID", str(exc), 422) from exc
        else:
            job.next_run_at = None
    await commit(db)
    if status == "enabled":
        await scheduler_service.reload_job(job.id)
    else:
        await scheduler_service.remove_job(job.id)
    return await get_job(db, job.id)


async def trigger_job(db: AsyncSession, ctx: AuthContext, job_id: int) -> dict[str, Any]:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("SCHEDULE_JOB_NOT_FOUND", status_code=404)
    if (job.job_type or "registry") == "python":
        _require_python_permission(ctx)
        _require_python_enabled()
        validate_python_script(job.script_code)
    else:
        validate_args(job.code, dict(job.args_json or {}))
    task = asyncio.create_task(run_job(job.id, "manual"))
    _background_tasks.add(task)
    task.add_done_callback(_consume_task)
    return {"accepted": True, "job_id": job.id}


async def next_runs(db: AsyncSession, job_id: int, count: int = 5) -> list[str]:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("SCHEDULE_JOB_NOT_FOUND", status_code=404)
    try:
        runs = preview_next_runs(job, count)
    except ValueError as exc:
        raise AppError("SCHEDULE_TRIGGER_INVALID", str(exc), 422) from exc
    return [iso(item) or "" for item in runs]


async def list_logs(
    db: AsyncSession,
    params: PageParams,
    *,
    keyword: str | None = None,
    job_id: int | None = None,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    statement = select(JobLog)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(JobLog.job_name).like(like, escape=LIKE_ESCAPE),
                JobLog.job_code.like(like, escape=LIKE_ESCAPE),
            )
        )
    if job_id:
        statement = statement.where(JobLog.job_id == job_id)
    if status:
        statement = statement.where(JobLog.status == status)
    statement = statement.order_by(JobLog.id.desc())
    rows, total = await paginate(db, statement, params)
    return [log_out(item) for item in rows], total


async def get_log(db: AsyncSession, log_id: int) -> dict[str, Any]:
    item = await db.get(JobLog, log_id)
    if item is None:
        raise AppError("SCHEDULE_LOG_NOT_FOUND", status_code=404)
    return log_out(item, include_detail=True)


async def list_templates(
    db: AsyncSession,
    *,
    job_type: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    statement = select(JobTemplate)
    if job_type:
        statement = statement.where(JobTemplate.job_type == job_type)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(JobTemplate.name).like(like, escape=LIKE_ESCAPE),
                JobTemplate.code.like(like, escape=LIKE_ESCAPE),
            )
        )
    items = (
        (await db.execute(statement.order_by(JobTemplate.is_builtin.desc(), JobTemplate.id)))
        .scalars()
        .all()
    )
    return [template_out(item) for item in items]


async def get_template(db: AsyncSession, template_id: int) -> dict[str, Any]:
    item = await db.get(JobTemplate, template_id)
    if item is None:
        raise AppError("SCHEDULE_TEMPLATE_NOT_FOUND", status_code=404)
    return template_out(item)


def _validate_template_definition(
    *,
    job_type: str,
    handler_code: str | None,
    script_code: str | None,
    args: dict[str, Any],
    trigger_type: str,
    cron_expr: str | None,
    interval_seconds: int | None,
    timezone: str,
    ctx: AuthContext,
) -> None:
    _validate_trigger_fields(
        trigger_type=trigger_type,
        cron_expr=cron_expr,
        interval_seconds=interval_seconds,
        run_at=None,
        timezone=timezone,
    )
    if job_type == "registry":
        if not handler_code:
            raise AppError("SCHEDULE_TEMPLATE_HANDLER_REQUIRED", status_code=422)
        get_definition(handler_code)
        validate_args(handler_code, args)
    elif job_type == "python":
        _require_python_permission(ctx)
        validate_python_script(script_code)
    else:
        raise AppError("SCHEDULE_JOB_TYPE_INVALID", status_code=422)


async def create_template(
    db: AsyncSession, ctx: AuthContext, data: JobTemplateCreate
) -> dict[str, Any]:
    exists = (
        await db.execute(select(JobTemplate.id).where(JobTemplate.code == data.code))
    ).scalar_one_or_none()
    if exists:
        raise AppError("SCHEDULE_TEMPLATE_CODE_EXISTS", status_code=409)
    zone_name = data.timezone or scheduler_timezone()
    _validate_template_definition(
        job_type=data.job_type,
        handler_code=data.handler_code,
        script_code=data.script_code,
        args=data.args_json,
        trigger_type=data.trigger_type,
        cron_expr=data.cron_expr,
        interval_seconds=data.interval_seconds,
        timezone=zone_name,
        ctx=ctx,
    )
    item = JobTemplate(
        code=data.code,
        name=data.name,
        description=data.description,
        job_type=data.job_type,
        handler_code=data.handler_code if data.job_type == "registry" else None,
        script_code=data.script_code if data.job_type == "python" else None,
        args_json=data.args_json,
        trigger_type=data.trigger_type,
        cron_expr=data.cron_expr if data.trigger_type == "cron" else None,
        interval_seconds=data.interval_seconds if data.trigger_type == "interval" else None,
        timezone=zone_name,
        timeout_seconds=data.timeout_seconds,
        is_builtin=False,
        created_by=ctx.user.id,
    )
    db.add(item)
    await commit(db)
    await db.refresh(item)
    return template_out(item)


async def update_template(
    db: AsyncSession, ctx: AuthContext, template_id: int, data: JobTemplateUpdate
) -> dict[str, Any]:
    item = await db.get(JobTemplate, template_id)
    if item is None:
        raise AppError("SCHEDULE_TEMPLATE_NOT_FOUND", status_code=404)
    if item.is_builtin:
        raise AppError("SCHEDULE_TEMPLATE_BUILTIN", "内置任务模板不可修改，可复制为自定义模板", 409)
    payload = data.model_dump(exclude_unset=True)
    job_type = payload.get("job_type", item.job_type)
    handler_code = payload.get("handler_code", item.handler_code)
    script_code = payload.get("script_code", item.script_code)
    args = payload.get("args_json", item.args_json or {})
    trigger_type = payload.get("trigger_type", item.trigger_type)
    cron_expr = payload.get("cron_expr", item.cron_expr)
    interval_seconds = payload.get("interval_seconds", item.interval_seconds)
    timezone = payload.get("timezone", item.timezone)
    _validate_template_definition(
        job_type=job_type,
        handler_code=handler_code,
        script_code=script_code,
        args=args or {},
        trigger_type=trigger_type,
        cron_expr=cron_expr,
        interval_seconds=interval_seconds,
        timezone=timezone,
        ctx=ctx,
    )
    for field, value in payload.items():
        setattr(item, field, value)
    item.job_type = job_type
    item.handler_code = handler_code if job_type == "registry" else None
    item.script_code = script_code if job_type == "python" else None
    item.cron_expr = cron_expr if trigger_type == "cron" else None
    item.interval_seconds = interval_seconds if trigger_type == "interval" else None
    await commit(db)
    await db.refresh(item)
    return template_out(item)


async def delete_template(db: AsyncSession, template_id: int) -> None:
    item = await db.get(JobTemplate, template_id)
    if item is None:
        raise AppError("SCHEDULE_TEMPLATE_NOT_FOUND", status_code=404)
    if item.is_builtin:
        raise AppError("SCHEDULE_TEMPLATE_BUILTIN", "内置任务模板不可删除", 409)
    await db.delete(item)
    await commit(db)


def definitions() -> list[dict[str, Any]]:
    return list_definitions()


__all__ = [
    "build_trigger",
    "create_job",
    "create_template",
    "definitions",
    "delete_job",
    "delete_template",
    "get_job",
    "get_log",
    "get_template",
    "job_out",
    "list_jobs",
    "list_logs",
    "list_templates",
    "next_runs",
    "set_job_state",
    "template_out",
    "trigger_job",
    "update_job",
    "update_template",
]
