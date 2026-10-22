from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete

from app.db.base import rowcount, utcnow
from app.db.session import session_scope
from app.jobs.registry import (
    CleanupFilesArgs,
    CleanupLogsArgs,
    CleanupSessionsArgs,
    HeartbeatArgs,
    JobDefinition,
    NotifyDispatchArgs,
    register,
)
from app.modules.audit.models import LoginLog, OperationLog
from app.modules.content.service import cleanup_orphan_files
from app.modules.notify import service as notify_service
from app.modules.schedule.models import JobLog
from app.modules.system.service import cleanup_old_sessions


async def cleanup_sessions(args: CleanupSessionsArgs) -> str:
    async with session_scope() as db:
        count = await cleanup_old_sessions(db, args.days)
    return f"deleted_sessions={count}"


async def cleanup_logs(args: CleanupLogsArgs) -> str:
    cutoff = utcnow() - timedelta(days=args.days)
    async with session_scope() as db:
        login_result = await db.execute(delete(LoginLog).where(LoginLog.created_at < cutoff))
        operation_result = await db.execute(delete(OperationLog).where(OperationLog.created_at < cutoff))
        job_result = await db.execute(delete(JobLog).where(JobLog.created_at < cutoff))
        notify_deleted = await notify_service.cleanup_records(db, args.days)
        await db.commit()
    return (
        f"deleted_login_logs={rowcount(login_result)};"
        f"deleted_operation_logs={rowcount(operation_result)};"
        f"deleted_job_logs={rowcount(job_result)};"
        f"deleted_notify_records={notify_deleted}"
    )


async def cleanup_files(args: CleanupFilesArgs) -> str:
    async with session_scope() as db:
        count = await cleanup_orphan_files(db, days=args.days)
    return f"deleted_files={count}"


async def heartbeat(args: HeartbeatArgs) -> str:
    return f"ok: {args.message}"


async def dispatch_notifications(args: NotifyDispatchArgs) -> str:
    counts = await notify_service.dispatch(limit=args.limit)
    return " ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def register_builtins() -> None:
    builtins: list[JobDefinition[Any]] = [
        JobDefinition(
            code="system.cleanup_sessions",
            name="清理过期会话",
            description="删除超过保留天数且已过期的登录会话",
            args_model=CleanupSessionsArgs,
            handler=cleanup_sessions,
        ),
        JobDefinition(
            code="system.cleanup_logs",
            name="清理过期日志",
            description="清理超过保留期的登录日志、操作日志、任务日志与通知发送记录",
            args_model=CleanupLogsArgs,
            handler=cleanup_logs,
        ),
        JobDefinition(
            code="system.cleanup_files",
            name="清理未引用文件",
            description="删除超过保留期且未被头像引用的上传文件，请谨慎启用",
            args_model=CleanupFilesArgs,
            handler=cleanup_files,
        ),
        JobDefinition(
            code="system.heartbeat",
            name="示例心跳任务",
            description="仅用于验证调度器是否正常运行，默认暂停",
            args_model=HeartbeatArgs,
            handler=heartbeat,
        ),
        JobDefinition(
            code="notify.dispatch",
            name="通知消息投递",
            description="把待发送的通知记录推送到已启用的 IM 渠道，可安全重复执行",
            args_model=NotifyDispatchArgs,
            handler=dispatch_notifications,
        ),
    ]
    for item in builtins:
        register(item)
