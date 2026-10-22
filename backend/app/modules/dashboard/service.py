from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.modules.audit.models import LoginLog
from app.modules.content.models import Notice
from app.modules.schedule.models import Job, JobLog
from app.modules.system.models import Dept, Role, User, UserSession


async def summary(db: AsyncSession) -> dict[str, Any]:
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=6)

    total_users = int((await db.execute(select(func.count(User.id)))).scalar() or 0)
    active_users = int(
        (await db.execute(select(func.count(User.id)).where(User.status == "active"))).scalar() or 0
    )
    total_depts = int((await db.execute(select(func.count(Dept.id)))).scalar() or 0)
    total_roles = int((await db.execute(select(func.count(Role.id)))).scalar() or 0)
    published_notices = int(
        (await db.execute(select(func.count(Notice.id)).where(Notice.status == "published"))).scalar() or 0
    )
    enabled_jobs = int(
        (await db.execute(select(func.count(Job.id)).where(Job.status == "enabled"))).scalar() or 0
    )
    online_users = int(
        (
            await db.execute(
                select(func.count(UserSession.id)).where(
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now,
                    UserSession.last_seen_at >= now - timedelta(minutes=3),
                )
            )
        ).scalar()
        or 0
    )
    today_success = int(
        (
            await db.execute(
                select(func.count(LoginLog.id)).where(
                    LoginLog.created_at >= today, LoginLog.status == "success"
                )
            )
        ).scalar()
        or 0
    )
    today_failure = int(
        (
            await db.execute(
                select(func.count(LoginLog.id)).where(
                    LoginLog.created_at >= today, LoginLog.status == "failure"
                )
            )
        ).scalar()
        or 0
    )
    failed_jobs_week = int(
        (
            await db.execute(
                select(func.count(JobLog.id)).where(
                    JobLog.created_at >= week_ago, JobLog.status == "failed"
                )
            )
        ).scalar()
        or 0
    )

    trend_rows = (
        await db.execute(
            select(func.date(LoginLog.created_at), LoginLog.status, func.count(LoginLog.id))
            .where(LoginLog.created_at >= week_ago)
            .group_by(func.date(LoginLog.created_at), LoginLog.status)
        )
    ).all()
    trend_map: dict[str, dict[str, int]] = {}
    for day, status, count in trend_rows:
        key = str(day)
        trend_map.setdefault(key, {"success": 0, "failure": 0})[str(status)] = int(cast(int, count))
    login_trend = []
    for offset in range(6, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        item = trend_map.get(day, {"success": 0, "failure": 0})
        login_trend.append({"date": day, "success": item.get("success", 0), "failure": item.get("failure", 0)})

    recent_logins = (
        (
            await db.execute(
                select(LoginLog).order_by(LoginLog.id.desc()).limit(6)
            )
        )
        .scalars()
        .all()
    )
    recent_job_logs = (
        (
            await db.execute(
                select(JobLog).order_by(JobLog.id.desc()).limit(6)
            )
        )
        .scalars()
        .all()
    )

    return {
        "stats": {
            "total_users": total_users,
            "active_users": active_users,
            "total_depts": total_depts,
            "total_roles": total_roles,
            "published_notices": published_notices,
            "enabled_jobs": enabled_jobs,
            "online_users": online_users,
            "today_login_success": today_success,
            "today_login_failure": today_failure,
            "failed_jobs_week": failed_jobs_week,
        },
        "login_trend": login_trend,
        "recent_logins": [
            {
                "id": item.id,
                "username": item.username,
                "status": item.status,
                "reason": item.reason,
                "ip": item.ip,
                "created_at": item.created_at.isoformat().replace("+00:00", "Z"),
            }
            for item in recent_logins
        ],
        "recent_job_logs": [
            {
                "id": item.id,
                "job_name": item.job_name,
                "job_code": item.job_code,
                "status": item.status,
                "started_at": item.started_at.isoformat().replace("+00:00", "Z"),
                "duration_ms": item.duration_ms,
            }
            for item in recent_job_logs
        ],
    }
