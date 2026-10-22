from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow

user_roles = Table(
    "sys_user_role",
    Base.metadata,
    Column("user_id", ForeignKey("sys_user.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "sys_role_permission",
    Base.metadata,
    Column("role_id", ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("sys_permission.id", ondelete="CASCADE"), primary_key=True),
)

role_depts = Table(
    "sys_role_dept",
    Base.metadata,
    Column("role_id", ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True),
    Column("dept_id", ForeignKey("sys_dept.id", ondelete="CASCADE"), primary_key=True),
)


class Dept(Base, TimestampMixin):
    __tablename__ = "sys_dept"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sys_dept.id", ondelete="RESTRICT"), index=True)
    ancestors: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    leader: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(32))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(500))


class User(Base, TimestampMixin):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    avatar_file_id: Mapped[int | None] = mapped_column(Integer)
    dept_id: Mapped[int | None] = mapped_column(ForeignKey("sys_dept.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # 封禁与「禁用」是两件事：禁用是业务状态，封禁是安全处置（可带到期时间与原因），
    # 封禁生效期间即使已有令牌也会被鉴权依赖拒绝。
    banned_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ban_reason: Mapped[str | None] = mapped_column(String(255))
    banned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_ip: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(500))

    dept: Mapped[Dept | None] = relationship(lazy="selectin")
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin", back_populates="users")

    @property
    def is_banned(self) -> bool:
        """是否处于封禁中。

        ``banned_at`` 是「有没有被封」的标志，``banned_until`` 只表示「封到什么时候」：
        永久封禁时 ``banned_until`` 为 NULL，所以不能拿它判空。
        """
        if self.banned_at is None:
            return False
        return self.banned_until is None or self.banned_until > utcnow()


class Role(Base, TimestampMixin):
    __tablename__ = "sys_role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    data_scope: Mapped[str] = mapped_column(String(32), default="all", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(500))

    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")
    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, lazy="selectin", back_populates="roles"
    )
    departments: Mapped[list[Dept]] = relationship(secondary=role_depts, lazy="selectin")


class Permission(Base):
    __tablename__ = "sys_permission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    module: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), default="api", nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    deprecated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    roles: Mapped[list[Role]] = relationship(secondary=role_permissions, back_populates="permissions")


class Menu(Base, TimestampMixin):
    __tablename__ = "sys_menu"
    __table_args__ = (Index("ix_sys_menu_parent_sort", "parent_id", "sort"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sys_menu.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(16), default="menu", nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    route_name: Mapped[str | None] = mapped_column(String(64))
    path: Mapped[str | None] = mapped_column(String(128))
    component_key: Mapped[str | None] = mapped_column(String(128))
    redirect: Mapped[str | None] = mapped_column(String(128))
    icon: Mapped[str | None] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    permission_code: Mapped[str | None] = mapped_column(String(128), index=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class UserSession(Base):
    __tablename__ = "sys_session"
    __table_args__ = (Index("ix_sys_session_user_revoked", "user_id", "revoked_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    previous_refresh_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
