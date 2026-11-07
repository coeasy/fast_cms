from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow


class JobTemplate(Base, TimestampMixin):
    __tablename__ = "sys_job_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    job_type: Mapped[str] = mapped_column(String(16), default="registry", nullable=False)
    handler_code: Mapped[str | None] = mapped_column(String(128))
    script_code: Mapped[str | None] = mapped_column(Text)
    args_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), default="cron", nullable=False)
    cron_expr: Mapped[str | None] = mapped_column(String(128))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer)


class Job(Base, TimestampMixin):
    __tablename__ = "sys_job"
    __table_args__ = (Index("ix_sys_job_status_next", "status", "next_run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(16), default="registry", nullable=False)
    script_code: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("sys_job_template.id", ondelete="SET NULL"), index=True
    )
    description: Mapped[str | None] = mapped_column(String(255))
    trigger_type: Mapped[str] = mapped_column(String(16), default="cron", nullable=False)
    cron_expr: Mapped[str | None] = mapped_column(String(128))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    args_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="enabled", nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(16))
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer)


class JobLog(Base):
    __tablename__ = "sys_job_log"
    __table_args__ = (Index("ix_sys_job_log_job_created", "job_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("sys_job.id", ondelete="SET NULL"), index=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    job_code: Mapped[str] = mapped_column(String(128), nullable=False)
    job_type: Mapped[str] = mapped_column(String(16), default="registry", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), default="cron", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False, index=True)
    run_token: Mapped[str | None] = mapped_column(String(64), index=True)
    code_hash: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)


class JobLock(Base, TimestampMixin):
    __tablename__ = "sys_job_lock"

    lock_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
