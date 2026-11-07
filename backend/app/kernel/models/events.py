"""事件投递与事务性发件箱模型（A-4：``core_event_log`` + outbox）。

两组表：

- ``EventLog``（``sys_event_log``）：轻量投递记录。``publish()`` 每次广播后
  为每个订阅者落一条（事件名、订阅者、成败、耗时），把「通知没发出去」从
  一行 error 日志变成可查询的审计轨迹（A-4 ①）。
- ``EventOutbox``（``sys_event_outbox``）：事务性发件箱。``publish()`` 在
  **所有订阅者都失败**时把事件写入（生产端），由默认启用的
  ``event.outbox_drain`` 定时任务调用 ``drain_event_outbox()`` 限次重试派发，
  获得至少一次投递 + 跨进程一致 + 请求路径非阻塞（A-4 ②）。
  **部分订阅者失败不进发件箱**：重放会重复调用已成功的订阅者；这类失败由
  ``EventLog`` 逐条留痕并打 WARNING，按业务补偿。

表名沿用平台 ``sys_`` 前缀（与 ``sys_operation_log`` 等一致）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, utcnow

# outbox 条目状态：pending=待派发，done=已派发，failed=重试超限
OUTBOX_STATUSES = ("pending", "done", "failed")
# 命名别名 + 终态集合：drain / 清理 / 监控都从这里取，新增状态时只需改上一行，
# 不会出现「状态清单加了、某个过滤条件还写死旧的三个值」这种静默漏项。
OUTBOX_PENDING, OUTBOX_DONE, OUTBOX_FAILED = OUTBOX_STATUSES
OUTBOX_TERMINAL_STATUSES = (OUTBOX_DONE, OUTBOX_FAILED)


class EventLog(Base):
    """一次事件广播中，单个订阅者的投递结果。"""

    __tablename__ = "sys_event_log"
    __table_args__ = (Index("ix_sys_event_log_created_event", "created_at", "event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subscriber: Mapped[str] = mapped_column(String(128), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    # payload 的轻量摘要（截断），用于排查「这个事件带了什么」
    payload_summary: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)


class EventOutbox(Base):
    """事务性发件箱条目：发布方在同一事务写入，dispatcher 随后派发。"""

    __tablename__ = "sys_event_outbox"
    __table_args__ = (
        Index("ix_sys_event_outbox_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
