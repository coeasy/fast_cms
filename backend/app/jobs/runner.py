from __future__ import annotations

import asyncio
import hashlib
import traceback
from contextlib import suppress
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.errors import AppError
from app.core.serializers import iso
from app.db.base import rowcount, utcnow
from app.db.session import session_scope
from app.jobs.python_runner import OUTPUT_LIMIT, PythonJobError, execute_python_script
from app.jobs.registry import execute_job
from app.modules.schedule.locks import acquire_locks, renew_with_retry
from app.modules.schedule.models import Job, JobLock, JobLog


def _job_snapshot(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "code": job.code,
        "name": job.name,
        "job_type": job.job_type or "registry",
        "script_code": job.script_code,
        "args": dict(job.args_json or {}),
        "timeout": int(job.timeout_seconds or settings.scheduler_job_timeout),
        "trigger_type": job.trigger_type,
        "status": job.status,
    }


def _code_hash(snapshot: dict[str, Any]) -> str | None:
    if snapshot["job_type"] != "python" or not snapshot.get("script_code"):
        return None
    return hashlib.sha256(snapshot["script_code"].encode("utf-8")).hexdigest()


async def _write_running_log(snapshot: dict[str, Any], trigger: str, token: str) -> int:
    async with session_scope() as db:
        log = JobLog(
            job_id=snapshot["id"],
            job_name=snapshot["name"],
            job_code=snapshot["code"],
            job_type=snapshot["job_type"],
            trigger_type=trigger,
            status="running",
            run_token=token,
            code_hash=_code_hash(snapshot),
            started_at=utcnow(),
        )
        db.add(log)
        await db.flush()
        return int(log.id)


async def _write_skipped_log(snapshot: dict[str, Any], trigger: str, reason: str) -> None:
    async with session_scope() as db:
        db.add(
            JobLog(
                job_id=snapshot["id"],
                job_name=snapshot["name"],
                job_code=snapshot["code"],
                job_type=snapshot["job_type"],
                trigger_type=trigger,
                status="skipped",
                started_at=utcnow(),
                finished_at=utcnow(),
                duration_ms=0,
                error=reason,
            )
        )


async def log_scheduler_skip(job_id: int, scheduled_at: datetime | None, reason: str) -> None:
    """Record a run the scheduler dropped before ``run_job`` was ever called."""
    async with session_scope() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        snapshot = _job_snapshot(job)
    suffix = f"（计划时间 {iso(scheduled_at)}）" if scheduled_at is not None else ""
    await _write_skipped_log(snapshot, "scheduler", f"{reason}{suffix}")


async def run_job(job_id: int, trigger: str = "scheduler") -> None:
    async with session_scope() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        if trigger == "scheduler" and job.status != "enabled":
            return
        snapshot = _job_snapshot(job)

    if snapshot["job_type"] == "python" and not settings.scheduler_python_enabled:
        await _write_skipped_log(snapshot, trigger, "Python 脚本任务已被全局禁用")
        return

    timeout = snapshot["timeout"]
    ttl = max(60, timeout + 60)
    lease = await acquire_locks([f"job:{job_id}"], ttl)
    if lease is None:
        await _write_skipped_log(snapshot, trigger, "已有同一任务在执行，本次跳过")
        return

    started = utcnow()
    log_id: int | None = None
    stop_event = asyncio.Event()
    lease_lost = asyncio.Event()
    renew_task: asyncio.Task[None] | None = None
    status = "success"
    output: str | None = None
    error: str | None = None
    try:
        log_id = await _write_running_log(snapshot, trigger, lease.token)
        async with session_scope() as db:
            current = await db.get(Job, job_id)
            if current is not None:
                current.last_run_at = started
                current.last_status = "running"
        renew_task = asyncio.create_task(
            renew_with_retry(lease, max(20, ttl // 3), stop_event, lease_lost)
        )
        if snapshot["job_type"] == "python":
            output = await execute_python_script(
                snapshot.get("script_code") or "",
                snapshot["args"],
                timeout,
                snapshot["code"],
            )
        else:
            result = await asyncio.wait_for(
                execute_job(snapshot["code"], snapshot["args"]),
                timeout=timeout,
            )
            output = str(result)[:OUTPUT_LIMIT] if result is not None else None
    except TimeoutError:
        status = "failed"
        error = f"任务执行超时（{timeout} 秒）"
    except asyncio.CancelledError:
        status = "interrupted"
        error = "任务被取消"
        raise
    except (PythonJobError, AppError) as exc:
        status = "failed"
        message = exc.message if isinstance(exc, AppError) else str(exc)
        error = message[:OUTPUT_LIMIT]
    except Exception:
        status = "failed"
        error = traceback.format_exc()[:OUTPUT_LIMIT]
    finally:
        if renew_task is not None:
            stop_event.set()
            renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await renew_task
        finished = utcnow()
        duration_ms = int((finished - started).total_seconds() * 1000)
        if lease_lost.is_set():
            note = "[warn] 任务锁续租失败，本次执行期间不保证与其它进程互斥"
            output = f"{output}\n{note}" if output else note
        try:
            async with session_scope() as db:
                if log_id is not None:
                    log = await db.get(JobLog, log_id)
                    if log is not None:
                        log.status = status
                        log.finished_at = finished
                        log.duration_ms = duration_ms
                        log.output = output
                        log.error = error
                current_job = await db.get(Job, job_id)
                if current_job is not None:
                    current_job.last_run_at = started
                    current_job.last_status = status
                    # 只有调度器跑完计划内执行才收尾停用；手动「立即执行」不能停用未到期的一次性任务
                    if snapshot["trigger_type"] == "date" and trigger == "scheduler" and current_job.status == "enabled":
                        current_job.status = "paused"
                        current_job.next_run_at = None
        finally:
            await lease.release()
        with suppress(Exception):
            from app.modules.schedule.scheduler import scheduler_service

            await scheduler_service.refresh_job_state(job_id)
        with suppress(Exception, asyncio.CancelledError):
            from app.modules.notify import hooks

            await hooks.job_finished(
                log_id=log_id,
                job=snapshot,
                status=status,
                error=error,
                duration_ms=duration_ms,
                trigger=trigger,
                started_at=started,
                finished_at=finished,
            )


async def recover_interrupted_logs() -> int:
    now = utcnow()
    async with session_scope() as db:
        locks = (await db.execute(select(JobLock))).scalars().all()
        stale_keys = [
            lock.lock_key
            for lock in locks
            if lock.expires_at < now or not _owner_alive(lock.owner)
        ]
        if stale_keys:
            await db.execute(delete(JobLock).where(JobLock.lock_key.in_(stale_keys)))
        active_tokens = {
            lock.run_token
            for lock in locks
            if lock.expires_at >= now and lock.lock_key not in stale_keys
        }

        running_logs = (await db.execute(select(JobLog).where(JobLog.status == "running"))).scalars().all()
        interrupted = 0
        active_job_ids: set[int] = set()
        for log in running_logs:
            if log.run_token and log.run_token in active_tokens:
                if log.job_id:
                    active_job_ids.add(log.job_id)
                continue
            log.status = "interrupted"
            log.finished_at = now
            log.error = "调度进程重启，任务执行被中断"
            interrupted += 1
        result = await db.execute(
            update(Job)
            .where(Job.last_status == "running", Job.id.notin_(active_job_ids))
            .values(last_status="interrupted")
        )
    return interrupted + rowcount(result)


def log_out(item: JobLog, *, include_detail: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id,
        "job_id": item.job_id,
        "job_name": item.job_name,
        "job_code": item.job_code,
        "job_type": item.job_type,
        "trigger_type": item.trigger_type,
        "status": item.status,
        "started_at": iso(item.started_at),
        "finished_at": iso(item.finished_at),
        "duration_ms": item.duration_ms,
        "created_at": iso(item.created_at),
    }
    if include_detail:
        data["output"] = item.output
        data["error"] = item.error
        data["code_hash"] = item.code_hash
    return data


def _owner_alive(owner: str) -> bool:
    from app.modules.schedule.locks import owner_alive

    return owner_alive(owner)
