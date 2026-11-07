from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete

from app.core import events
from app.db.base import rowcount, utcnow
from app.db.session import session_scope
from app.jobs.registry import (
    CleanupFilesArgs,
    CleanupLogsArgs,
    CleanupSessionsArgs,
    EventOutboxDrainArgs,
    HeartbeatArgs,
    JobDefinition,
    NotifyDispatchArgs,
    register,
)
from app.kernel.files import cleanup_orphan_files
from app.kernel.models import EventLog, EventOutbox, LoginLog, OperationLog
from app.kernel.models.events import OUTBOX_TERMINAL_STATUSES
from app.modules.ops.notify import service as notify_service
from app.modules.ops.schedule.locks import acquire_locks
from app.modules.ops.schedule.models import JobLog
from app.modules.sys.system.service import cleanup_old_sessions


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
        # 事件投递记录和已终结的发件箱条目也会无限增长：done/failed 是终态，
        # pending 必须保留（还在重试），否则会把待派发事件连同日志一起删掉。
        event_result = await db.execute(delete(EventLog).where(EventLog.created_at < cutoff))
        outbox_result = await db.execute(
            delete(EventOutbox).where(
                EventOutbox.created_at < cutoff,
                EventOutbox.status.in_(OUTBOX_TERMINAL_STATUSES),
            )
        )
        notify_deleted = await notify_service.cleanup_records(db, args.days)
        await db.commit()
    return (
        f"deleted_login_logs={rowcount(login_result)};"
        f"deleted_operation_logs={rowcount(operation_result)};"
        f"deleted_job_logs={rowcount(job_result)};"
        f"deleted_event_logs={rowcount(event_result)};"
        f"deleted_outbox_rows={rowcount(outbox_result)};"
        f"deleted_notify_records={notify_deleted}"
    )


async def cleanup_files(args: CleanupFilesArgs) -> str:
    async with session_scope() as db:
        count = await cleanup_orphan_files(db, days=args.days)
    return f"deleted_files={count}"


async def heartbeat(args: HeartbeatArgs) -> str:
    return f"ok: {args.message}"


async def dispatch_notifications(args: NotifyDispatchArgs) -> str | None:
    counts = await notify_service.dispatch(limit=args.limit)
    if not any(counts.values()):
        # 本轮没有任何待投递/重试记录：返回 None 并配合 quiet_when_idle，
        # 让这次空转不留任务日志（每 60 秒一条空日志没有排查价值）。
        return None
    return " ".join(f"{key}={value}" for key, value in sorted(counts.items()))


async def drain_event_outbox_job(args: EventOutboxDrainArgs) -> str | None:
    """派发事务性事件发件箱（A-4 ② / 3-2）。

    多 worker 部署下多个调度进程可能同时轮询，这里用分布式锁保证同一时刻只有一个
    进程真正派发：锁拿不到就本轮空转（返回 None 不留日志），避免事件被重复投递。
    """
    lease = await acquire_locks(["event:outbox_drain"], ttl=60)
    if lease is None:
        return None
    try:
        async with session_scope() as db:
            count = await events.drain_event_outbox(db, limit=args.limit)
            await db.commit()
    finally:
        await lease.release()
    if count == 0:
        return None
    return f"dispatched={count}"


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
            quiet_when_idle=True,
        ),
        JobDefinition(
            code="event.outbox_drain",
            name="事件发件箱派发",
            description="周期性派发事务性事件发件箱里的待投递事件；多实例用分布式锁去重",
            args_model=EventOutboxDrainArgs,
            handler=drain_event_outbox_job,
            quiet_when_idle=True,
        ),
    ]
    for item in builtins:
        register(item)
