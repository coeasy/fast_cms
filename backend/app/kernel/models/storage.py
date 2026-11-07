"""存储模型：``FileObject``。

上传文件的对象记录属于平台能力：用户头像、个人资料、公告附件与「文件管理」页面
共用同一张表，因此放在内核层而不是某个业务模块里。

表名与列定义与原 ``kernel/models.py`` 完全一致，移动位置不产生任何迁移。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, utcnow


class FileObject(Base):
    """上传文件的对象记录。

    属于平台能力：用户头像、个人资料、公告附件与「文件管理」页面共用同一张表，
    因此放在内核层而不是某个业务模块里。
    """

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
