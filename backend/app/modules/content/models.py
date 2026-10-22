from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow


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


class FileObject(Base):
    __tablename__ = "sys_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
