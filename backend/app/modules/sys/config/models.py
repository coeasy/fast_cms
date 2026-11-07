from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class DictType(Base, TimestampMixin):
    __tablename__ = "sys_dict_type"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))

    items: Mapped[list[DictItem]] = relationship(
        back_populates="dict_type", cascade="all, delete-orphan", lazy="selectin"
    )


class DictItem(Base, TimestampMixin):
    __tablename__ = "sys_dict_item"
    __table_args__ = (UniqueConstraint("dict_type_id", "value", name="uq_dict_item_type_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dict_type_id: Mapped[int] = mapped_column(ForeignKey("sys_dict_type.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    tag_color: Mapped[str | None] = mapped_column(String(32))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))

    dict_type: Mapped[DictType] = relationship(back_populates="items")


class SystemConfig(Base, TimestampMixin):
    __tablename__ = "sys_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    type: Mapped[str] = mapped_column(String(16), default="string", nullable=False)
    group: Mapped[str] = mapped_column(String(64), default="basic", nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))
