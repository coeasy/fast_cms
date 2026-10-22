from __future__ import annotations

from datetime import datetime

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import success
from app.core.pagination import PageParams, page_data
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.audit import service
from app.modules.audit.schemas import IdBatchRequest

router = protected_router(
    prefix="/monitor",
    tags=["审计日志"],
    dependencies=[Depends(require_admin)],
)


@router.get("/login-logs", summary="登录日志", permission="monitor:login-log:list")
async def list_login_logs(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(success|failure)$"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_login_logs(
        db, ctx, params, keyword=keyword, status=status, start=start, end=end
    )
    return success(page_data(items, total, params))


@router.get("/login-logs/export", summary="导出登录日志", permission="monitor:login-log:export")
async def export_login_logs(
    keyword: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(success|failure)$"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    response, count = await service.export_login_logs(
        db, ctx, keyword=keyword, status=status, start=start, end=end
    )
    await service.write_export_audit(
        db,
        user_id=ctx.user.id,
        username=ctx.user.username,
        module="monitor",
        action="login-log.export",
        path="/monitor/login-logs/export",
        detail=f"rows={count}",
    )
    return response


@router.get("/login-logs/{log_id}", summary="登录日志详情", permission="monitor:login-log:list")
async def get_login_log(
    log_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_login_log(db, ctx, log_id))


@router.delete("/login-logs", summary="清理登录日志", permission="monitor:login-log:delete")
async def cleanup_login_logs(
    before_days: int = Query(default=90, ge=7, le=3650),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count = await service.cleanup_login_logs(db, ctx, before_days)
    return success({"deleted": count}, f"已清理 {count} 条登录日志")


@router.delete("/login-logs/batch", summary="批量删除登录日志", permission="monitor:login-log:batch")
async def batch_delete_login_logs(
    data: IdBatchRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await service.batch_delete_login_logs(db, ctx, data.ids)
    ok = len(result["succeeded"])
    failed = len(result["failed"])
    message = f"已删除 {ok} 条登录日志" + (f"，{failed} 条失败" if failed else "")
    return success(result, message)


@router.get("/operation-logs", summary="操作日志", permission="monitor:operation-log:list")
async def list_operation_logs(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=64),
    module: str | None = Query(default=None, max_length=64),
    success_flag: bool | None = Query(default=None, alias="success"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_operation_logs(
        db, ctx, params, keyword=keyword, module=module, success=success_flag, start=start, end=end
    )
    return success(page_data(items, total, params))


@router.get("/operation-logs/export", summary="导出操作日志", permission="monitor:operation-log:export")
async def export_operation_logs(
    keyword: str | None = Query(default=None, max_length=64),
    module: str | None = Query(default=None, max_length=64),
    success_flag: bool | None = Query(default=None, alias="success"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    response, count = await service.export_operation_logs(
        db,
        ctx,
        keyword=keyword,
        module=module,
        success=success_flag,
        start=start,
        end=end,
    )
    await service.write_export_audit(
        db,
        user_id=ctx.user.id,
        username=ctx.user.username,
        module="monitor",
        action="operation-log.export",
        path="/monitor/operation-logs/export",
        detail=f"rows={count}",
    )
    return response


@router.get("/operation-logs/{log_id}", summary="操作日志详情", permission="monitor:operation-log:list")
async def get_operation_log(
    log_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_operation_log(db, ctx, log_id))


@router.delete("/operation-logs/batch", summary="批量删除操作日志", permission="monitor:operation-log:batch")
async def batch_delete_operation_logs(
    data: IdBatchRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await service.batch_delete_operation_logs(db, ctx, data.ids)
    ok = len(result["succeeded"])
    failed = len(result["failed"])
    message = f"已删除 {ok} 条操作日志" + (f"，{failed} 条失败" if failed else "")
    return success(result, message)


@router.delete("/operation-logs", summary="清理操作日志", permission="monitor:operation-log:delete")
async def cleanup_operation_logs(
    before_days: int = Query(default=90, ge=7, le=3650),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count = await service.cleanup_operation_logs(db, ctx, before_days)
    return success({"deleted": count}, f"已清理 {count} 条操作日志")
