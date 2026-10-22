"""notify channels and send records

Revision ID: 583727afcb2f
Revises: e3f4a5b6c7d8
Create Date: 2026-10-20 22:26:45.010214
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = "583727afcb2f"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_notify_channel",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=16), nullable=False),
        sa.Column("webhook_url", sa.Text(), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_notify_channel")),
        sa.UniqueConstraint("name", name=op.f("uq_sys_notify_channel_name")),
    )
    with op.batch_alter_table("sys_notify_channel", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sys_notify_channel_status"), ["status"], unique=False)

    op.create_table(
        "sys_notify_record",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("channel_name", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=16), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("sent_at", app.db.base.UTCDateTime(), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.db.base.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["sys_notify_channel.id"],
            name=op.f("fk_sys_notify_record_channel_id_sys_notify_channel"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_notify_record")),
        sa.UniqueConstraint(
            "channel_id", "event", "dedupe_key", name=op.f("uq_sys_notify_record_channel_event_key")
        ),
    )
    with op.batch_alter_table("sys_notify_record", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sys_notify_record_channel_id"), ["channel_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_sys_notify_record_event"), ["event"], unique=False)
        batch_op.create_index(batch_op.f("ix_sys_notify_record_status"), ["status"], unique=False)
        batch_op.create_index("ix_sys_notify_record_status_next", ["status", "next_retry_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("sys_notify_record", schema=None) as batch_op:
        batch_op.drop_index("ix_sys_notify_record_status_next")
        batch_op.drop_index(batch_op.f("ix_sys_notify_record_status"))
        batch_op.drop_index(batch_op.f("ix_sys_notify_record_event"))
        batch_op.drop_index(batch_op.f("ix_sys_notify_record_channel_id"))

    op.drop_table("sys_notify_record")
    with op.batch_alter_table("sys_notify_channel", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sys_notify_channel_status"))

    op.drop_table("sys_notify_channel")
