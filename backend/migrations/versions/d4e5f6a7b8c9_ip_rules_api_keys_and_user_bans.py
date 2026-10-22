"""ip rules, api keys and user bans

Revision ID: d4e5f6a7b8c9
Revises: 583727afcb2f
Create Date: 2026-10-21 22:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "583727afcb2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_ip_rule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", app.db.base.UTCDateTime(), nullable=True),
        sa.Column("auto", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(length=64), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_ip_rule")),
    )
    with op.batch_alter_table("sys_ip_rule", schema=None) as batch_op:
        batch_op.create_index(
            "ix_sys_ip_rule_kind_status", ["kind", "status"], unique=False
        )
        batch_op.create_index(
            "uq_sys_ip_rule_target_scope", ["target", "scope"], unique=True
        )

    op.create_table(
        "sys_api_key",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", app.db.base.UTCDateTime(), nullable=True),
        sa.Column("last_used_at", app.db.base.UTCDateTime(), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=True),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(length=64), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_api_key")),
        sa.UniqueConstraint("name", name=op.f("uq_sys_api_key_name")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_sys_api_key_key_hash")),
    )
    with op.batch_alter_table("sys_api_key", schema=None) as batch_op:
        batch_op.create_index(
            "ix_sys_api_key_prefix_status", ["key_prefix", "status"], unique=False
        )

    with op.batch_alter_table("sys_user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("banned_until", app.db.base.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("ban_reason", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("banned_at", app.db.base.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sys_user", schema=None) as batch_op:
        batch_op.drop_column("banned_at")
        batch_op.drop_column("ban_reason")
        batch_op.drop_column("banned_until")

    with op.batch_alter_table("sys_api_key", schema=None) as batch_op:
        batch_op.drop_index("ix_sys_api_key_prefix_status")

    op.drop_table("sys_api_key")

    with op.batch_alter_table("sys_ip_rule", schema=None) as batch_op:
        batch_op.drop_index("uq_sys_ip_rule_target_scope")
        batch_op.drop_index("ix_sys_ip_rule_kind_status")

    op.drop_table("sys_ip_rule")
