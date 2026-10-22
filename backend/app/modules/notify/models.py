from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow

CHANNEL_TYPES = ("dingtalk", "feishu", "wecom", "webhook")
# 触发点只有任务执行结果与公告发布两类，其余 test 由渠道测试按钮写入。
SUBSCRIBABLE_EVENTS = ("job.failed", "job.interrupted", "notice.published")
RECORD_EVENTS = SUBSCRIBABLE_EVENTS + ("test",)
RECORD_STATUSES = ("pending", "sending", "success", "failed", "skipped")


class NotifyChannel(Base, TimestampMixin):
    __tablename__ = "sys_notify_channel"
    __table_args__ = (UniqueConstraint("name", name="uq_sys_notify_channel_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(16), default="webhook", nullable=False)
    # webhook 地址自带 access_token 等凭证，接口只返回脱敏值，不回显原文。
    webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str | None] = mapped_column(String(255))
    events: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="enabled", nullable=False, index=True)
    remark: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[int | None] = mapped_column(Integer)


class NotifyRecord(Base, TimestampMixin):
    __tablename__ = "sys_notify_record"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "event", "dedupe_key", name="uq_sys_notify_record_channel_event_key"
        ),
        Index("ix_sys_notify_record_status_next", "status", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("sys_notify_channel.id", ondelete="SET NULL"), index=True
    )
    channel_name: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(16), default="webhook", nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512))
    response_status: Mapped[int | None] = mapped_column(Integer)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
