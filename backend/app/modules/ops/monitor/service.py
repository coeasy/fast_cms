from __future__ import annotations

import asyncio
import os
import platform
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import psutil
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import ipgeo
from app.core.authz import AuthContext, scope_user_ids
from app.core.config import settings
from app.core.search import LIKE_ESCAPE, contains
from app.core.serializers import iso
from app.core.tz import current_timezone, scheduler_timezone
from app.db.base import utcnow
from app.db.session import database_dialect
from app.kernel.models import User, UserSession
from app.kernel.serializers import session_out, user_brief

ONLINE_WINDOW = timedelta(minutes=3)


def online_out(
    session: UserSession,
    user: User,
    *,
    current_id: str | None,
    location: str | None = None,
) -> dict[str, Any]:
    data = session_out(session, current=session.id == current_id)
    brief = user_brief(user)
    # user_brief 里也有 ``id``（用户 id），直接 update 会把会话 id 覆盖掉：
    # 列表的 ``id`` 必须是会话 id——「强制下线」按它定位会话，被覆盖后会去踢
    # 一个不存在的会话（或别人的）。用户 id 另给 ``user_id``。
    brief.pop("id", None)
    data.update(brief)
    data["user_id"] = user.id
    # 会话 IP 的归属地：读取时解析，与登录日志的「历史事实」不同，
    # 这里要的是「这个会话现在从哪来」
    data["location"] = location
    return data


async def list_online(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    now = utcnow()
    statement = (
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .options(selectinload(User.dept))
        .where(
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            UserSession.last_seen_at >= now - ONLINE_WINDOW,
            User.status == "active",
        )
        .order_by(UserSession.last_seen_at.desc())
    )
    allowed = await scope_user_ids(db, ctx)
    if allowed is not None:
        statement = statement.where(UserSession.user_id.in_(allowed))
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            func.lower(User.username).like(like, escape=LIKE_ESCAPE)
            | func.lower(User.nickname).like(like, escape=LIKE_ESCAPE)
        )
    rows = (await db.execute(statement)).all()
    # API Key 身份没有会话，current_id 传 None 表示「没有当前会话可高亮」
    current_id = ctx.session.id if ctx.session is not None else None
    locations = await ipgeo.locate_many(session.ip for session, _ in rows)
    return [
        online_out(session, user, current_id=current_id, location=ipgeo.label_of(locations, session.ip))
        for session, user in rows
    ]


def _server_info_sync() -> dict[str, Any]:
    # 注意：本函数是**同步阻塞**的——``psutil.cpu_percent(interval=0.1)`` 会
    # 睡 100ms，virtual_memory / disk_usage 也有毫秒级 syscall。事件循环上
    # 禁止直接调用，一律走下面的 ``server_info_async``。

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(".")
    process = psutil.Process()
    load1: float | None
    load5: float | None
    load15: float | None
    try:
        load1, load5, load15 = psutil.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = None
    return {
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "hostname": platform.node(),
            "python": sys.version.split()[0],
            "boot_time": iso(datetime.fromtimestamp(psutil.boot_time(), tz=UTC)),
            "uptime_seconds": int(time.time() - psutil.boot_time()),
        },
        "cpu": {
            "cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "percent": psutil.cpu_percent(interval=0.1),
            "load_avg": [round(item, 2) if item is not None else None for item in (load1, load5, load15)],
        },
        "memory": {
            "total": memory.total,
            "available": memory.available,
            "used": memory.used,
            "percent": memory.percent,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        "process": {
            "pid": process.pid,
            "memory_rss": process.memory_info().rss,
            "threads": process.num_threads(),
            "created_at": iso(datetime.fromtimestamp(process.create_time(), tz=UTC)),
        },
        "runtime": {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "git_sha": os.getenv("GIT_SHA", ""),
            "database": database_dialect(),
            "scheduler_mode": settings.scheduler_mode,
            "timezone": current_timezone(),
            "scheduler_timezone": scheduler_timezone(),
        },
    }


async def server_info_async() -> dict[str, Any]:
    """把 psutil 全家桶丢进线程池（B-2 / Phase 11-1）。

    监控页在高峰期也会被打开，而 ``cpu_percent(interval=0.1)`` 每次调用都
    同步睡 100ms——放在事件循环上等于「每刷新一次监控页，全站停 100ms」。
    """
    return await asyncio.to_thread(_server_info_sync)
