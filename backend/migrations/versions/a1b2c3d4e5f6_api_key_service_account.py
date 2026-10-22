"""api key service account binding

Revision ID: a1b2c3d4e5f6
Revises: 583727afcb2f
Create Date: 2026-10-22 08:10:00.000000

API Key 需要一个可归属的用户行：数据范围、审计署名、封禁都建立在
``sys_user`` 上。没有这一列，Key 验过去也只是「一个能调接口的字符串」，
既拿不到数据范围也写不进审计，等于把鉴权链路断在半路。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sys_api_key", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_sys_api_key_user_id_sys_user"),
            "sys_user",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sys_api_key", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_sys_api_key_user_id_sys_user"), type_="foreignkey"
        )
        batch_op.drop_column("user_id")
