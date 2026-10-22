from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ipgeo
from app.core.authz import AuthContext, DataScope, apply_user_scope, get_data_scope
from app.core.config import api_path
from app.core.errors import AppError, get_request_id
from app.core.pagination import PageParams, paginate
from app.core.request_context import current_context
from app.core.serializers import iso
from app.core.tz import local_text
from app.db.base import rowcount, utcnow
from app.modules.audit.models import LoginLog, OperationLog
from app.modules.system.models import User

MAX_EXPORT_ROWS = 10000


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
    statement,  # type: ignore[no-untyped-def]
    *,
    keyword: str | None,
    status: str | None,
    start: datetime | None,
    end: datetime | None,
):  # type: ignore[no-untyped-def]
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(
            or_(func.lower(LoginLog.username).like(like), LoginLog.ip.like(like))
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
    statement,  # type: ignore[no-untyped-def]
    ctx: AuthContext,
    scope: DataScope | None,
    column,  # type: ignore[no-untyped-def]
    *,
    include_orphan: bool = True,
):  # type: ignore[no-untyped-def]
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
) -> tuple[list[dict[str, Any]], int]:
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


async def get_login_log(db: AsyncSession, ctx: AuthContext, log_id: int) -> dict[str, Any]:
    item = await db.get(LoginLog, log_id)
    scope = await _log_scope(db, ctx)
    if item is None or not await _row_visible(db, ctx, scope, item.user_id):
        raise AppError("AUDIT_LOG_NOT_FOUND", "登录日志不存在", 404)
    return login_log_out(item)


def _operation_filters(
    statement,  # type: ignore[no-untyped-def]
    *,
    keyword: str | None,
    module: str | None,
    success: bool | None,
    start: datetime | None,
    end: datetime | None,
):  # type: ignore[no-untyped-def]
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(
            or_(
                func.lower(OperationLog.username).like(like),
                OperationLog.path.like(like),
                OperationLog.action.like(like),
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
) -> tuple[list[dict[str, Any]], int]:
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
        raise AppError("AUDIT_BATCH_EMPTY", "请先选择要删除的日志", 422)
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
    await db.commit()
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
    if before_days < 7:
        raise AppError("AUDIT_RETENTION_TOO_SHORT", "至少保留 7 天日志", 422)
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
    await db.commit()
    return rowcount(result)


async def cleanup_operation_logs(db: AsyncSession, ctx: AuthContext, before_days: int) -> int:
    if before_days < 7:
        raise AppError("AUDIT_RETENTION_TOO_SHORT", "至少保留 7 天日志", 422)
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
    await db.commit()
    return rowcount(result)


def _csv_response(rows: Iterable[list[Any]], header: list[str], filename: str):  # type: ignore[no-untyped-def]
    from fastapi.responses import StreamingResponse

    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    data = buffer.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def export_login_logs(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    keyword: str | None = None,
    status: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[Any, int]:
    start, end = _normalize_range(start, end)
    scope = await _log_scope(db, ctx)
    statement = _login_filters(
        _restrict_to_scope(select(LoginLog), ctx, scope, LoginLog.user_id),
        keyword=keyword,
        status=status,
        start=start,
        end=end,
    ).order_by(LoginLog.id.desc()).limit(MAX_EXPORT_ROWS)
    rows: Sequence[LoginLog] = (await db.execute(statement)).scalars().all()
    response = _csv_response(
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
        ["ID", "用户名", "结果", "原因", "IP", "归属地", "User-Agent", "时间"],
        "login-logs.csv",
    )
    return response, len(rows)


async def export_operation_logs(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    keyword: str | None = None,
    module: str | None = None,
    success: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[Any, int]:
    start, end = _normalize_range(start, end)
    scope = await _log_scope(db, ctx)
    statement = _operation_filters(
        _restrict_to_scope(select(OperationLog), ctx, scope, OperationLog.user_id),
        keyword=keyword,
        module=module,
        success=success,
        start=start,
        end=end,
    ).order_by(OperationLog.id.desc()).limit(MAX_EXPORT_ROWS)
    rows: Sequence[OperationLog] = (await db.execute(statement)).scalars().all()
    locations = await ipgeo.locate_many(item.ip for item in rows)
    response = _csv_response(
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
        ["ID", "用户", "模块", "动作", "方法", "路径", "状态码", "结果", "耗时(ms)", "IP", "归属地", "时间"],
        "operation-logs.csv",
    )
    return response, len(rows)


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
            duration_ms=0,
            ip=http.ip,
            user_agent=http.user_agent,
            detail=detail,
            request_id=get_request_id() or None,
        )
    )
    # 必须自己提交：get_db 依赖不会 commit，导出审计以前就是在这里被静默丢掉的。
    # 谁导出了什么数据是安全审计的一环，丢了比没有更糟。
    await db.commit()
    await db.commit()
