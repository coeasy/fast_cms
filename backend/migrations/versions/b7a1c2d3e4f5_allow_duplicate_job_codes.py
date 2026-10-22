"""allow duplicate job codes

Revision ID: b7a1c2d3e4f5
Revises: c9ce1cf0f9c2
Create Date: 2026-10-20 14:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7a1c2d3e4f5"
down_revision: str | None = "c9ce1cf0f9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sys_job", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sys_job_code"))
        batch_op.create_index(batch_op.f("ix_sys_job_code"), ["code"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("sys_job", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sys_job_code"))
        batch_op.create_index(batch_op.f("ix_sys_job_code"), ["code"], unique=True)
