from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import rowcount, utcnow
from app.db.session import session_scope
from app.modules.ops.schedule.models import JobLock

logger = logging.getLogger("fastcms.scheduler.locks")


def lock_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owner_alive(owner: str) -> bool:
    host, _, rest = owner.partition(":")
    pid_text = rest.split(":", 1)[0]
    try:
        pid = int(pid_text)
    except ValueError:
        return True
    if host.lower() != socket.gethostname().lower():
        return True
    return _pid_alive(pid)


class JobLease:
    def __init__(self, keys: list[str], token: str, ttl: int, owner: str) -> None:
        self.keys = keys
        self.token = token
        self.ttl = ttl
        self.owner = owner

    async def renew(self) -> bool:
        now = utcnow()
        async with session_scope() as db:
            result = await db.execute(
                update(JobLock)
                .where(
                    JobLock.lock_key.in_(self.keys),
                    JobLock.run_token == self.token,
                    JobLock.expires_at > now,
                )
                .values(expires_at=now + timedelta(seconds=self.ttl))
            )
        return rowcount(result) == len(self.keys)

    async def release(self) -> None:
        async with session_scope() as db:
            await db.execute(
                delete(JobLock).where(
                    JobLock.lock_key.in_(self.keys),
                    JobLock.run_token == self.token,
                )
            )


async def acquire_locks(keys: list[str], ttl: int) -> JobLease | None:
    """单事务批量申请：要么全部拿到，要么一个都不留，绝无部分锁残留。

    - MySQL：``SELECT ... FOR UPDATE`` 行锁串行化并发申请；
    - SQLite：单写者事务天然串行化，``FOR UPDATE`` 被方言忽略；
    - 已过期锁在事务内被抢占；并发方若在 SELECT 与 INSERT 之间抢到某 key，
      INSERT 冲突触发 ``IntegrityError``，整批回滚后返回 None（失败可定位）。
    """
    token = uuid.uuid4().hex
    owner = lock_owner()
    now = utcnow()
    expires_at = now + timedelta(seconds=ttl)
    unique_keys = list(dict.fromkeys(keys))
    try:
        async with session_scope() as db:
            existing = (
                await db.execute(
                    select(JobLock).where(JobLock.lock_key.in_(unique_keys)).with_for_update()
                )
            ).scalars().all()
            if any(lock.expires_at >= now for lock in existing):
                return None
            if existing:
                await db.execute(
                    delete(JobLock).where(JobLock.lock_key.in_([lock.lock_key for lock in existing]))
                )
            for key in unique_keys:
                await db.execute(
                    insert(JobLock).values(
                        lock_key=key,
                        run_token=token,
                        owner=owner,
                        expires_at=expires_at,
                        created_at=now,
                        updated_at=now,
                    )
                )
    except IntegrityError:
        return None
    return JobLease(keys=unique_keys, token=token, ttl=ttl, owner=owner)


async def purge_expired_locks(now: datetime | None = None) -> int:
    moment = now or utcnow()
    async with session_scope() as db:
        locks = (await db.execute(select(JobLock))).scalars().all()
        stale_keys = [
            lock.lock_key
            for lock in locks
            if lock.expires_at < moment or not owner_alive(lock.owner)
        ]
        if not stale_keys:
            return 0
        result = await db.execute(delete(JobLock).where(JobLock.lock_key.in_(stale_keys)))
    return rowcount(result)


async def renew_with_retry(
    lease: JobLease,
    interval: int,
    stop: asyncio.Event,
    lost: asyncio.Event | None = None,
) -> None:
    last_ok = utcnow()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            if not await lease.renew():
                logger.warning("job lock lease lost owner=%s keys=%s", lease.owner, lease.keys)
                # 租约丢了就无法再保证互斥，通知调用方把这一轮标记为「可能并发」留痕
                if lost is not None:
                    lost.set()
                return
            last_ok = utcnow()
        except Exception as exc:
            logger.warning("job lock renewal failed keys=%s error=%s", lease.keys, exc)
            # 连续失败超过一个 TTL 意味着租约在库里已经过期，别的进程随时可能接手。
            # 只记 warning 不标记 lost，本进程会继续跑同一轮任务 —— 表现为任务被执行两次，
            # 而日志里没有任何「可能并发」的痕迹。
            if (utcnow() - last_ok).total_seconds() > lease.ttl:
                logger.error(
                    "job lock 超过 TTL 未能续约，标记为可能并发 owner=%s keys=%s",
                    lease.owner,
                    lease.keys,
                )
                if lost is not None:
                    lost.set()
                return
