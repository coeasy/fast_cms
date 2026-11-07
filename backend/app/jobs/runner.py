from __future__ import annotations

import asyncio
import hashlib
import logging
import traceback
from contextlib import suppress
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from app.core import events
from app.core.config import settings
from app.core.errors import AppError
from app.core.serializers import iso
from app.db.base import rowcount, utcnow
from app.db.session import session_scope
from app.jobs import registry
from app.jobs.python_runner import OUTPUT_LIMIT, PythonJobError, execute_python_script
from app.jobs.registry import execute_job
from app.modules.ops.schedule.locks import acquire_locks, renew_with_retry
from app.modules.ops.schedule.models import Job, JobLock, JobLog

logger = logging.getLogger("fastcms.jobs")


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


def _is_idle_noop(
    snapshot: dict[str, Any],
    status: str,
    output: str | None,
    error: str | None,
) -> bool:
    """本次执行是否属于「无事可做」的空转轮次。

    三个条件同时成立才算：任务显式声明了 ``quiet_when_idle``、执行成功、
    且处理器没有产出（返回 None）。失败或中断永远留日志——那是要排查的东西。
    """
    if status != "success" or output or error:
        return False
    return registry.is_quiet_when_idle(str(snapshot.get("code") or ""))


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
    idle_noop = False
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
            idle_noop = _is_idle_noop(snapshot, status, output, error)
            async with session_scope() as db:
                if log_id is not None:
                    log = await db.get(JobLog, log_id)
                    if log is not None:
                        if idle_noop:
                            # 高频 interval 任务的空转轮次不留日志：否则一天上千条
                            # 「成功」会把任务日志页面刷满，真正的失败反而被淹没。
                            await db.delete(log)
                        else:
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
        if idle_noop:
            # 日志行已删，事件里不能再带一个指向空行的 log_id
            log_id = None
        try:
            from app.modules.ops.schedule.scheduler import scheduler_service

            await scheduler_service.refresh_job_state(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 收尾刷新失败会让 next_run_at 停在旧值，页面上看不出任何异常；
            # 必须留日志，否则「任务跑完了但下次时间不对」无从排查。
            logger.warning("刷新任务状态失败 job_id=%s", job_id, exc_info=True)
        try:
            # 只广播「任务结束了」，通知渠道由 notify 模块自己订阅
            await events.publish(
                "job.finished",
                log_id=log_id,
                job=snapshot,
                status=status,
                error=error,
                duration_ms=duration_ms,
                trigger=trigger,
                started_at=started,
                finished_at=finished,
            )
        except asyncio.CancelledError:
            # CancelledError 在 3.12 是 BaseException：停机取消若恰好命中这个
            # await，绝不能吞掉，否则 run_job 会「正常返回」、取消方的 cancel()
            # 拿不到异常，任务也不会被标记中断。
            raise
        except Exception:
            # 不影响任务结果，但不能静默：publish 抛出意味着「任务结束」通知
            # 连 EventLog 都没留下，留一行可查的告警而不是黑洞。
            logger.warning("job.finished 广播失败 job_id=%s", job_id, exc_info=True)


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
    from app.modules.ops.schedule.locks import owner_alive

    return owner_alive(owner)
