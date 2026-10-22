"""job templates, execution locks and theme templates

Revision ID: e3f4a5b6c7d8
Revises: b7a1c2d3e4f5
Create Date: 2026-10-20 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "b7a1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_theme",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_theme")),
    )
    with op.batch_alter_table("sys_theme", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sys_theme_code"), ["code"], unique=True)

    op.create_table(
        "sys_job_template",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("job_type", sa.String(length=16), nullable=False),
        sa.Column("handler_code", sa.String(length=128), nullable=True),
        sa.Column("script_code", sa.Text(), nullable=True),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("trigger_type", sa.String(length=16), nullable=False),
        sa.Column("cron_expr", sa.String(length=128), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_job_template")),
    )
    with op.batch_alter_table("sys_job_template", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sys_job_template_code"), ["code"], unique=True)

    with op.batch_alter_table("sys_job", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("job_type", sa.String(length=16), nullable=False, server_default="registry")
        )
        batch_op.add_column(sa.Column("script_code", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("template_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_sys_job_template_id"), ["template_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_sys_job_template_id_sys_job_template"),
            "sys_job_template",
            ["template_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("sys_job_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("job_type", sa.String(length=16), nullable=False, server_default="registry")
        )
        batch_op.add_column(sa.Column("run_token", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("code_hash", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_sys_job_log_run_token"), ["run_token"], unique=False)

    op.create_table(
        "sys_job_lock",
        sa.Column("lock_key", sa.String(length=160), nullable=False),
        sa.Column("run_token", sa.String(length=64), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=False),
        sa.Column("expires_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("created_at", app.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("lock_key", name=op.f("pk_sys_job_lock")),
    )
    with op.batch_alter_table("sys_job_lock", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sys_job_lock_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_sys_job_lock_run_token"), ["run_token"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("sys_job_lock", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sys_job_lock_run_token"))
        batch_op.drop_index(batch_op.f("ix_sys_job_lock_expires_at"))
    op.drop_table("sys_job_lock")

    with op.batch_alter_table("sys_job_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sys_job_log_run_token"))
        batch_op.drop_column("code_hash")
        batch_op.drop_column("run_token")
        batch_op.drop_column("job_type")

    with op.batch_alter_table("sys_job", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_sys_job_template_id_sys_job_template"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_sys_job_template_id"))
        batch_op.drop_column("template_id")
        batch_op.drop_column("script_code")
        batch_op.drop_column("job_type")

    with op.batch_alter_table("sys_job_template", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sys_job_template_code"))
    op.drop_table("sys_job_template")

    with op.batch_alter_table("sys_theme", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sys_theme_code"))
    op.drop_table("sys_theme")
