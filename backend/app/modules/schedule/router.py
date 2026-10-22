from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.permissions import ADMIN_PERMISSION
from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.config import settings
from app.core.errors import success
from app.core.pagination import PageParams, page_data
from app.core.routing import protected_router
from app.db.session import get_db
from app.jobs.python_runner import validate_python_script
from app.modules.schedule import service
from app.modules.schedule.schemas import (
    JobCreate,
    JobTemplateCreate,
    JobTemplateUpdate,
    JobUpdate,
    PythonScriptPayload,
)

router = protected_router(
    prefix="/schedule",
    tags=["定时任务"],
    dependencies=[Depends(require_admin)],
)


@router.get("/capabilities", summary="调度能力", permission=ADMIN_PERMISSION)
async def capabilities(ctx: AuthContext = Depends(get_current_user)):
    return success(
        {
            "python_enabled": settings.scheduler_python_enabled,
            "can_manage_python": ctx.has("schedule:job:code"),
        }
    )


@router.post("/python/validate", summary="校验 Python 脚本", permission="schedule:job:code")
async def validate_python(
    data: PythonScriptPayload,
    ctx: AuthContext = Depends(get_current_user),
):
    validate_python_script(data.script_code)
    return success({"valid": True}, "脚本语法校验通过")


@router.get("/jobs", summary="任务列表", permission="schedule:job:list")
async def list_jobs(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, pattern="^(enabled|paused)$"),
    job_type: str | None = Query(default=None, pattern="^(registry|python)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_jobs(
        db, params, keyword=keyword, status=status, job_type=job_type
    )
    return success(page_data(items, total, params))


@router.get("/jobs/definitions", summary="可用任务定义", permission="schedule:job:list")
async def job_definitions(ctx: AuthContext = Depends(get_current_user)):
    return success(service.definitions())


@router.post("/jobs", summary="创建任务", permission="schedule:job:create")
async def create_job(
    data: JobCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_job(db, ctx, data), "任务已创建")


@router.get("/jobs/{job_id}", summary="任务详情", permission="schedule:job:list")
async def get_job(
    job_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(
        await service.get_job(db, job_id, include_script=ctx.has("schedule:job:code"))
    )


@router.put("/jobs/{job_id}", summary="更新任务", permission="schedule:job:update")
async def update_job(
    job_id: int,
    data: JobUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_job(db, ctx, job_id, data), "任务已更新")


@router.delete("/jobs/{job_id}", summary="删除任务", permission="schedule:job:delete")
async def delete_job(
    job_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_job(db, job_id)
    return success(None, "任务已删除")


@router.post("/jobs/{job_id}/pause", summary="暂停任务", permission="schedule:job:state")
async def pause_job(
    job_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.set_job_state(db, ctx, job_id, "paused"), "任务已暂停")


@router.post("/jobs/{job_id}/resume", summary="恢复任务", permission="schedule:job:state")
async def resume_job(
    job_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.set_job_state(db, ctx, job_id, "enabled"), "任务已恢复")


@router.post("/jobs/{job_id}/trigger", summary="立即执行任务", permission="schedule:job:trigger")
async def trigger_job(
    job_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.trigger_job(db, ctx, job_id), "任务已提交执行")


@router.get("/jobs/{job_id}/next-runs", summary="预览下次执行时间", permission="schedule:job:list")
async def next_runs(
    job_id: int,
    count: int = Query(default=5, ge=1, le=10),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.next_runs(db, job_id, count))


@router.get("/templates", summary="任务模板列表", permission="schedule:template:list")
async def list_templates(
    keyword: str | None = Query(default=None, max_length=128),
    job_type: str | None = Query(default=None, pattern="^(registry|python)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_templates(db, job_type=job_type, keyword=keyword))


@router.post("/templates", summary="创建任务模板", permission="schedule:template:create")
async def create_template(
    data: JobTemplateCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_template(db, ctx, data), "任务模板已创建")


@router.get("/templates/{template_id}", summary="任务模板详情", permission="schedule:template:list")
async def get_template(
    template_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_template(db, template_id))


@router.put("/templates/{template_id}", summary="更新任务模板", permission="schedule:template:update")
async def update_template(
    template_id: int,
    data: JobTemplateUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_template(db, ctx, template_id, data), "任务模板已更新")


@router.delete("/templates/{template_id}", summary="删除任务模板", permission="schedule:template:delete")
async def delete_template(
    template_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_template(db, template_id)
    return success(None, "任务模板已删除")


@router.get("/job-logs", summary="任务执行日志", permission="schedule:log:list")
async def list_logs(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=128),
    job_id: int | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(running|success|failed|skipped|interrupted)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_logs(
        db, params, keyword=keyword, job_id=job_id, status=status
    )
    return success(page_data(items, total, params))


@router.get("/job-logs/{log_id}", summary="任务日志详情", permission="schedule:log:list")
async def get_log(
    log_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_log(db, log_id))
