from __future__ import annotations

from sqlalchemy import JSON, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ThemeTemplate(Base, TimestampMixin):
    __tablename__ = "sys_theme"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
