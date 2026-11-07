from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ipgeo
from app.core.authz import AuthContext, DataScope, apply_user_scope, get_data_scope
from app.core.config import api_path
from app.core.errors import AppError, get_request_id
from app.core.export import MAX_EXPORT_ROWS, csv_response
from app.core.logging import log_exception
from app.core.pagination import PageParams, keyset_paginate, paginate
from app.core.request_context import current_context, request_duration_ms
from app.core.search import LIKE_ESCAPE, contains
from app.core.serializers import iso
from app.core.tz import local_text
from app.db.base import rowcount, utcnow
from app.kernel.db import commit
from app.kernel.models import EventLog, LoginLog, OperationLog, User

logger = logging.getLogger("app.modules.ops.audit")

# 日志清理的保留下限：小于这个天数会拒绝，避免「手滑把近一周的审计记录清空」。
# 校验与错误文案（MESSAGES 的 {days} 占位符）共用这一处，改下限只改这里。
MIN_RETENTION_DAYS = 7


def login_log_out(item: LoginLog) -> dict[str, Any]:
    return {
        "id": item.id,
        "username": item.username,
        "user_id": item.user_id,
        "status": item.status,
        "reason": item.reason,
        "ip": item.ip,
        # 登录时定型的归属地；换 xdb 不会改写历史，NULL 表示写入时库不可用
        "location": item.location,
        "user_agent": item.user_agent,
        "created_at": iso(item.created_at),
    }


def operation_log_out(
    item: OperationLog,
    *,
    include_detail: bool = False,
    location: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id,
        "user_id": item.user_id,
        "username": item.username,
        "module": item.module,
        "action": item.action,
        "method": item.method,
        "path": item.path,
        "status_code": item.status_code,
        "success": item.success,
        "duration_ms": item.duration_ms,
        "ip": item.ip,
        # 读取时解析：操作日志没有 location 列（存量行太多，回填会让库变大），
        # 归属地是「当前视角的补充信息」，与登录日志的「历史事实」语义不同
        "location": location,
        "user_agent": item.user_agent,
        "request_id": item.request_id,
        "created_at": iso(item.created_at),
    }
    if include_detail:
        data["detail"] = item.detail
    return data


def _normalize_range(start: datetime | None, end: datetime | None) -> tuple[datetime | None, datetime | None]:
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return start, end


def _login_filters(
    statement,
    *,
    keyword: str | None,
    status: str | None,
    start: datetime | None,
    end: datetime | None,
):
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(LoginLog.username).like(like, escape=LIKE_ESCAPE),
                LoginLog.ip.like(like, escape=LIKE_ESCAPE),
            )
        )
    if status:
        statement = statement.where(LoginLog.status == status)
    if start:
        statement = statement.where(LoginLog.created_at >= start)
    if end:
        statement = statement.where(LoginLog.created_at <= end)
    return statement


async def _log_scope(db: AsyncSession, ctx: AuthContext) -> DataScope | None:
    """返回 ``None`` 表示该用户可见全部日志，否则返回需要生效的数据范围。"""
    if ctx.is_superuser:
        return None
    scope = await get_data_scope(db, ctx)
    return None if scope.all_access else scope


def _restrict_to_scope(
    statement,
    ctx: AuthContext,
    scope: DataScope | None,
    column,
    *,
    include_orphan: bool = True,
):
    """按数据范围过滤日志；未关联账号的记录（如爆破未知用户名）默认对所有人可见。

    ``include_orphan=False`` 用于清理：这类行往往是攻击流量，只允许超管/全量范围删除，
    部门管理员不能销毁自己不拥有的审计证据。
    """
    if scope is None:
        return statement
    scoped_users = column.in_(apply_user_scope(select(User.id), ctx, scope))
    if include_orphan:
        return statement.where(or_(column.is_(None), scoped_users))
    return statement.where(scoped_users)


async def _row_visible(db: AsyncSession, ctx: AuthContext, scope: DataScope | None, user_id: int | None) -> bool:
    if scope is None or user_id is None:
        return True
    if scope.include_self and user_id == ctx.user.id:
        return True
    if not scope.dept_ids:
        return False
    target = await db.get(User, user_id)
    return bool(target and target.dept_id in scope.dept_ids)


async def list_login_logs(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
    *,
    keyword: str | None = None,
    status: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    start, end = _normalize_range(start, end)
    scope = await _log_scope(db, ctx)
    statement = _login_filters(
        _restrict_to_scope(select(LoginLog), ctx, scope, LoginLog.user_id),
        keyword=keyword,
        status=status,
        start=start,
        end=end,
    ).order_by(LoginLog.id.desc())
    rows, total = await paginate(db, statement, params)
    return [login_log_out(item) for item in rows], total


async def list_login_logs_keyset(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    cursor: int | None = None,
    limit: int = 20,
    keyword: str | None = None,
    status: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    """登录日志的游标分页版（P1-5 keyset 落地）。

    与 ``list_login_logs`` 共用过滤条件，但按 ``id`` 降序游标翻页：
    不执行全量 ``count``、不做深偏移扫描，适合高频大表深分页。
    返回 ``(items, next_cursor)``，``next_cursor`` 为 ``None`` 表示没有更多数据。
    """
    start, end = _normalize_range(start, end)
    scope = await _log_scope(db, ctx)
    statement = _login_filters(
        _restrict_to_scope(select(LoginLog), ctx, scope, LoginLog.user_id),
        keyword=keyword,
        status=status,
        start=start,
        end=end,
    )
    rows, next_cursor = await keyset_paginate(
        db, statement, key_col=LoginLog.id, cursor=cursor, limit=limit, ascending=False
    )
    return [login_log_out(item) for item in rows], next_cursor


async def get_login_log(db: AsyncSession, ctx: AuthContext, log_id: int) -> dict[str, Any]:
    item = await db.get(LoginLog, log_id)
    scope = await _log_scope(db, ctx)
    if item is None or not await _row_visible(db, ctx, scope, item.user_id):
        raise AppError("AUDIT_LOG_NOT_FOUND", "登录日志不存在", 404)
    return login_log_out(item)


def _operation_filters(
    statement,
    *,
    keyword: str | None,
    module: str | None,
    success: bool | None,
    start: datetime | None,
    end: datetime | None,
):
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(OperationLog.username).like(like, escape=LIKE_ESCAPE),
                OperationLog.path.like(like, escape=LIKE_ESCAPE),
                OperationLog.action.like(like, escape=LIKE_ESCAPE),
            )
        )
    if module:
        statement = statement.where(OperationLog.module == module)
    if success is not None:
        statement = statement.where(OperationLog.success.is_(success))
    if start:
        statement = statement.where(OperationLog.created_at >= start)
    if end:
        statement = statement.where(OperationLog.created_at <= end)
    return statement


async def list_operation_logs(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
    *,
    keyword: str | None = None,
    module: str | None = None,
    success: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    start, end = _normalize_range(start, end)
    scope = await _log_scope(db, ctx)
    statement = _operation_filters(
        _restrict_to_scope(select(OperationLog), ctx, scope, OperationLog.user_id),
        keyword=keyword,
        module=module,
        success=success,
        start=start,
        end=end,
    ).order_by(OperationLog.id.desc())
    rows, total = await paginate(db, statement, params)
    locations = await ipgeo.locate_many(item.ip for item in rows)
    return [
        operation_log_out(item, location=ipgeo.label_of(locations, item.ip)) for item in rows
    ], total


async def get_operation_log(db: AsyncSession, ctx: AuthContext, log_id: int) -> dict[str, Any]:
    item = await db.get(OperationLog, log_id)
    scope = await _log_scope(db, ctx)
    if item is None or not await _row_visible(db, ctx, scope, item.user_id):
        raise AppError("AUDIT_LOG_NOT_FOUND", "操作日志不存在", 404)
    locations = await ipgeo.locate_many([item.ip])
    return operation_log_out(
        item, include_detail=True, location=ipgeo.label_of(locations, item.ip)
    )


def event_log_out(item: EventLog) -> dict[str, Any]:
    """事件投递记录序列化（A-4 ①：管理端可查）。"""
    return {
        "id": item.id,
        "event": item.event,
        "subscriber": item.subscriber,
        "success": item.success,
        "duration_ms": item.duration_ms,
        "error": item.error,
        "payload_summary": item.payload_summary,
        "created_at": iso(item.created_at),
    }


async def list_event_logs(
    db: AsyncSession,
    params: PageParams,
    *,
    event: str | None = None,
    success: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    """分页查询事件投递记录。不涉及数据范围（事件日志属平台审计，非业务行）。"""
    start, end = _normalize_range(start, end)
    statement = select(EventLog)
    if event:
        statement = statement.where(EventLog.event == event)
    if success is not None:
        statement = statement.where(EventLog.success == success)
    if start is not None:
        statement = statement.where(EventLog.created_at >= start)
    if end is not None:
        statement = statement.where(EventLog.created_at <= end)
    statement = statement.order_by(EventLog.id.desc())
    rows, total = await paginate(db, statement, params)
    return [event_log_out(item) for item in rows], total


MAX_BATCH_DELETE_IDS = 500


async def _batch_delete(
    db: AsyncSession,
    ctx: AuthContext,
    model: Any,
    ids: list[int],
    *,
    user_id_column: Any,
    action: str,
) -> dict[str, Any]:
    """按 id 批量删除日志；同样遵循「单条失败不影响其他」。"""
    if not ids:
        raise AppError("AUDIT_BATCH_EMPTY", status_code=422)
    if len(ids) > MAX_BATCH_DELETE_IDS:
        raise AppError("AUDIT_BATCH_TOO_MANY", f"单次最多删除 {MAX_BATCH_DELETE_IDS} 条", 422)

    scope = await _log_scope(db, ctx)
    succeeded: list[int] = []
    failed: list[dict[str, Any]] = []
    for log_id in dict.fromkeys(ids):
        item = await db.get(model, log_id)
        if item is None or not await _row_visible(db, ctx, scope, item.user_id):
            failed.append({"id": log_id, "code": "AUDIT_LOG_NOT_FOUND", "message": "日志不存在或无权访问"})
            continue
        statement = _restrict_to_scope(
            delete(model).where(model.id == log_id),
            ctx,
            scope,
            user_id_column,
            include_orphan=False,
        )
        result = await db.execute(statement)
        if rowcount(result):
            succeeded.append(log_id)
        else:
            failed.append({"id": log_id, "code": "AUDIT_LOG_NOT_FOUND", "message": "日志不存在或无权访问"})

    await write_export_audit(
        db,
        user_id=ctx.user.id,
        username=ctx.user.username,
        module="monitor",
        action=action,
        path=f"/monitor/{action}",
        detail=f"total={len(ids)} ok={len(succeeded)} failed={len(failed)}",
    )
    await commit(db)
    return {"succeeded": succeeded, "failed": failed}


async def batch_delete_login_logs(db: AsyncSession, ctx: AuthContext, ids: list[int]) -> dict[str, Any]:
    return await _batch_delete(
        db, ctx, LoginLog, ids, user_id_column=LoginLog.user_id, action="login-log.batch-delete"
    )


async def batch_delete_operation_logs(db: AsyncSession, ctx: AuthContext, ids: list[int]) -> dict[str, Any]:
    return await _batch_delete(
        db, ctx, OperationLog, ids, user_id_column=OperationLog.user_id, action="operation-log.batch-delete"
    )


async def cleanup_login_logs(db: AsyncSession, ctx: AuthContext, before_days: int) -> int:
    if before_days < MIN_RETENTION_DAYS:
        raise AppError("AUDIT_RETENTION_TOO_SHORT", status_code=422, params={"days": MIN_RETENTION_DAYS})
    cutoff = utcnow() - timedelta(days=before_days)
    scope = await _log_scope(db, ctx)
    statement = _restrict_to_scope(
        delete(LoginLog).where(LoginLog.created_at < cutoff),
        ctx,
        scope,
        LoginLog.user_id,
        include_orphan=False,
    )
    result = await db.execute(statement)
    await commit(db)
    return rowcount(result)


async def cleanup_operation_logs(db: AsyncSession, ctx: AuthContext, before_days: int) -> int:
    if before_days < MIN_RETENTION_DAYS:
        raise AppError("AUDIT_RETENTION_TOO_SHORT", status_code=422, params={"days": MIN_RETENTION_DAYS})
    cutoff = utcnow() - timedelta(days=before_days)
    scope = await _log_scope(db, ctx)
    statement = _restrict_to_scope(
        delete(OperationLog).where(OperationLog.created_at < cutoff),
        ctx,
        scope,
        OperationLog.user_id,
        include_orphan=False,
    )
    result = await db.execute(statement)
    await commit(db)
    return rowcount(result)


async def export_login_logs(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    keyword: str | None = None,
    status: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[Any, int, bool]:
    start, end = _normalize_range(start, end)
    scope = await _log_scope(db, ctx)
    statement = _login_filters(
        _restrict_to_scope(select(LoginLog), ctx, scope, LoginLog.user_id),
        keyword=keyword,
        status=status,
        start=start,
        end=end,
    ).order_by(LoginLog.id.desc())
    # 多取一行判断截断，与 core/export.MAX_EXPORT_ROWS 单源一致；
    # 完整结果交给 csv_response，由它作为唯一截断语义来源
    statement = statement.limit(MAX_EXPORT_ROWS + 1)
    rows: Sequence[LoginLog] = (await db.execute(statement)).scalars().all()
    response, count, truncated = csv_response(
        ["ID", "用户名", "结果", "原因", "IP", "归属地", "User-Agent", "时间"],
        (
            [
                item.id,
                item.username,
                item.status,
                item.reason or "",
                item.ip or "",
                item.location or "",
                item.user_agent or "",
                local_text(item.created_at, "%Y-%m-%d %H:%M:%S%z"),
            ]
            for item in rows
        ),
        "login-logs.csv",
    )
    return response, count, truncated


async def export_operation_logs(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    keyword: str | None = None,
    module: str | None = None,
    success: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[Any, int, bool]:
    start, end = _normalize_range(start, end)
    scope = await _log_scope(db, ctx)
    statement = _operation_filters(
        _restrict_to_scope(select(OperationLog), ctx, scope, OperationLog.user_id),
        keyword=keyword,
        module=module,
        success=success,
        start=start,
        end=end,
    ).order_by(OperationLog.id.desc()).limit(MAX_EXPORT_ROWS + 1)
    rows: Sequence[OperationLog] = (await db.execute(statement)).scalars().all()
    locations = await ipgeo.locate_many(item.ip for item in rows)
    response, count, truncated = csv_response(
        ["ID", "用户", "模块", "动作", "方法", "路径", "状态码", "结果", "耗时(ms)", "IP", "归属地", "时间"],
        (
            [
                item.id,
                item.username or "",
                item.module or "",
                item.action or "",
                item.method,
                item.path,
                item.status_code,
                "成功" if item.success else "失败",
                item.duration_ms,
                item.ip or "",
                ipgeo.label_of(locations, item.ip) or "",
                local_text(item.created_at, "%Y-%m-%d %H:%M:%S%z"),
            ]
            for item in rows
        ),
        "operation-logs.csv",
    )
    return response, count, truncated


async def write_export_audit(
    db: AsyncSession,
    *,
    user_id: int,
    username: str,
    module: str,
    action: str,
    path: str,
    detail: str,
) -> None:
    http = current_context()
    db.add(
        OperationLog(
            user_id=user_id,
            username=username,
            module=module,
            action=action,
            method="GET",
            path=api_path(path),
            status_code=200,
            success=True,
            duration_ms=request_duration_ms(),
            ip=http.ip,
            user_agent=http.user_agent,
            detail=detail,
            request_id=get_request_id() or None,
        )
    )
    # 必须自己提交：get_db 依赖不会 commit，导出审计以前就是在这里被静默丢掉的。
    # 谁导出了什么数据是安全审计的一环，丢了比没有更糟。
    # 只 commit 一次：P0-6 修复此前连续两次 commit 的重复提交。
    # 不可降级：审计写入失败必须记录上下文并上抛（P2-3 异常分级规范）。
    try:
        await commit(db)
    except Exception as exc:
        log_exception(
            logger,
            exc,
            level=logging.ERROR,
            context={
                "user_id": user_id,
                "username": username,
                "module": module,
                "action": action,
                "path": path,
            },
        )
        raise
