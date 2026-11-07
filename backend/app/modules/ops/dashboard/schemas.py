"""工作台响应模型：与 ``ops/dashboard/service.summary()`` 的返回结构一一对应。"""

from __future__ import annotations

from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    total_depts: int
    total_roles: int
    published_notices: int
    enabled_jobs: int
    online_users: int
    today_login_success: int
    today_login_failure: int
    failed_jobs_week: int


class LoginTrendPoint(BaseModel):
    date: str
    success: int
    failure: int


class RecentLogin(BaseModel):
    id: int
    username: str
    status: str
    reason: str | None = None
    ip: str | None = None
    created_at: str


class RecentJobLog(BaseModel):
    id: int
    job_name: str
    job_code: str
    status: str
    started_at: str
    duration_ms: int | None = None


class DashboardSummary(BaseModel):
    stats: DashboardStats
    login_trend: list[LoginTrendPoint]
    recent_logins: list[RecentLogin]
    recent_job_logs: list[RecentJobLog]
