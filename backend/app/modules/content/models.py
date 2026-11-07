from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow

# FileObject 已迁至内核层（app/kernel/models.py）：文件存储是平台能力，
# 头像 / 个人资料 / 文件管理页面都直接用它，不属于公告业务。
# 这里保留 re-export 一个版本，避免外部引用一次性断裂。
from app.kernel.models import FileObject  # noqa: F401


class Notice(Base, TimestampMixin):
    __tablename__ = "sys_notice"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    is_top: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[int | None] = mapped_column(Integer)


class NoticeRead(Base):
    __tablename__ = "sys_notice_read"
    __table_args__ = (UniqueConstraint("notice_id", "user_id", name="uq_notice_read_notice_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("sys_notice.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE"), index=True)
    read_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
