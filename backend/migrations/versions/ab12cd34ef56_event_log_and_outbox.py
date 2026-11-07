"""event log and transactional outbox

Revision ID: ab12cd34ef56
Revises: f6a7b8c9d0e1
Create Date: 2026-10-29 09:00:00.000000

A-4：事件总线此前是「进程内同步、失败只记一行日志」的 at-most-once 语义。
补两张表把投递变成可追溯、可跨进程：

- ``sys_event_log``：``publish()`` 每次广播后为每个订阅者落一条（事件名、订阅者、
  成败、耗时），「通知没发出去」从一行日志变成可查询的审计轨迹。
- ``sys_event_outbox``：事务性发件箱。发布方在同一事务 ``enqueue_event()`` 写入，
  由 ``jobs/runner`` 轮询的 dispatcher 派发，获得至少一次投递 + 跨进程一致 +
  请求路径非阻塞。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = "ab12cd34ef56"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_event_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("subscriber", sa.String(length=128), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload_summary", sa.String(length=255), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sys_event_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sys_event_log_event"), ["event"], unique=False)
        batch_op.create_index(batch_op.f("ix_sys_event_log_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_sys_event_log_created_event"), ["created_at", "event"], unique=False
        )

    op.create_table(
        "sys_event_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("processed_at", app.db.base.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sys_event_outbox", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sys_event_outbox_event"), ["event"], unique=False)
        batch_op.create_index(batch_op.f("ix_sys_event_outbox_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_sys_event_outbox_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_sys_event_outbox_status_created"), ["status", "created_at"], unique=False
        )


def downgrade() -> None:
    op.drop_table("sys_event_outbox")
    op.drop_table("sys_event_log")
