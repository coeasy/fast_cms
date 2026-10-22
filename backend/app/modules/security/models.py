from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UTCDateTime

# block=拒绝，allow=放行；allow 优先级高于 block，用来给「整段封禁但运维要进」留白名单
IP_RULE_KINDS = ("block", "allow")

# 规则生效范围：all=整站，api=只拦接口，admin/portal=只拦对应页面与静态资源
IP_RULE_SCOPES = ("all", "api", "admin", "portal")

IP_RULE_STATUSES = ("active", "disabled")


class IpRule(Base, TimestampMixin):
    """IP / CIDR 访问控制规则。

    ``target`` 支持单 IP（``203.0.113.7``）、CIDR（``203.0.113.0/24``）
    和 IPv6（``2001:db8::/32``）；非法值在写入前就会被拒绝，
    因此中间件里可以放心地按预编译网段匹配。
    """

    __tablename__ = "sys_ip_rule"
    __table_args__ = (
        Index("ix_sys_ip_rule_kind_status", "kind", "status"),
        # 同一网段同一范围只留一条，避免规则列表出现互相矛盾的重复项
        Index("uq_sys_ip_rule_target_scope", "target", "scope", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="all", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    # 空表示永久有效；到期后中间件自动忽略，不需要定时任务来清理
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # 自动封禁产生的规则会打上标记，方便与人工规则区分并批量清理
    auto: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str | None] = mapped_column(String(64))


class ApiKey(Base, TimestampMixin):
    """第三方对接用的长期凭证。

    只存 ``sha256``，明文仅在创建响应里返回一次；``key_prefix`` 用于按前缀定位候选行，
    避免全表逐条比对哈希。
    """

    __tablename__ = "sys_api_key"
    __table_args__ = (Index("ix_sys_api_key_prefix_status", "key_prefix", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # 逗号分隔的权限码；``*`` 表示拥有全部权限（仅建议给内部系统）
    scopes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str | None] = mapped_column(String(64))
    # 绑定的服务账号。API Key 本身不是用户，但数据范围、审计归属、封禁都要落在
    # 一个真实用户行上，否则 62 处 ctx.user.* 调用点全都得为「没有用户」开特例。
    # 服务账号由创建 Key 时自动派生，禁止直接登录（无口令且 status 由 Key 驱动）。
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sys_user.id", ondelete="SET NULL"), nullable=True
    )
