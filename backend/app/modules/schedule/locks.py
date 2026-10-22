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
from app.modules.schedule.models import JobLock

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
        except Exception:  # noqa: BLE001
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


async def _acquire_one(key: str, token: str, owner: str, ttl: int) -> bool:
    now = utcnow()
    expires_at = now + timedelta(seconds=ttl)
    async with session_scope() as db:
        try:
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
            return True
        except IntegrityError:
            await db.rollback()
    async with session_scope() as db:
        result = await db.execute(
            update(JobLock)
            .where(JobLock.lock_key == key, JobLock.expires_at < now)
            .values(
                run_token=token,
                owner=owner,
                expires_at=expires_at,
                updated_at=now,
            )
        )
    return rowcount(result) == 1


async def acquire_locks(keys: list[str], ttl: int) -> JobLease | None:
    token = uuid.uuid4().hex
    owner = lock_owner()
    acquired: list[str] = []
    for key in dict.fromkeys(keys):
        if await _acquire_one(key, token, owner, ttl):
            acquired.append(key)
            continue
        if acquired:
            async with session_scope() as db:
                await db.execute(
                    delete(JobLock).where(
                        JobLock.lock_key.in_(acquired),
                        JobLock.run_token == token,
                    )
                )
        return None
    return JobLease(keys=acquired, token=token, ttl=ttl, owner=owner)


async def active_lock_tokens(tokens: set[str]) -> set[str]:
    if not tokens:
        return set()
    now = utcnow()
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(JobLock.run_token, JobLock.owner).where(
                    JobLock.run_token.in_(tokens),
                    JobLock.expires_at > now,
                )
            )
        ).all()
    return {token for token, owner in rows if owner_alive(owner)}


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
        except Exception as exc:  # noqa: BLE001
            logger.warning("job lock renewal failed keys=%s error=%s", lease.keys, exc)
