from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, utcnow


class LoginLog(Base):
    __tablename__ = "sys_login_log"
    __table_args__ = (Index("ix_sys_login_log_created_user", "created_at", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(String(64))
    # 登录时的 IP 归属地（ip2region 解析结果）。写入时定型：
    # 之后换了 xdb 也不该改写已经发生过的登录记录，否则审计看到的历史是假的。
    # 存量行为 NULL，界面显示「未知」。
    location: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)


class OperationLog(Base):
    __tablename__ = "sys_operation_log"
    __table_args__ = (Index("ix_sys_operation_log_created_user", "created_at", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(64))
    module: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(128))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
