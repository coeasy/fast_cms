"""encrypt notify channel secret at rest

Revision ID: f6a7b8c9d0e1
Revises: b2c3d4e5f6a7
Create Date: 2026-10-27 12:00:00.000000

``sys_notify_channel.secret`` 改为静态加密存储（app/core/crypto.py）。
加密后的密文长度超过明文上限（String(255)），固定长度列会在 MySQL 截断、
在 SQLite 中虽不强制但仍应修正类型语义，因此改为 ``Text``。

历史明文值保留可解密回退窗口（decrypt_secret 对无 ``enc:v1`` 前缀的值原样返回），
无需在迁移中改写数据；新写入的 secret 一律加密。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sys_notify_channel", schema=None) as batch_op:
        batch_op.alter_column("secret", type_=sa.Text(), existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("sys_notify_channel", schema=None) as batch_op:
        batch_op.alter_column("secret", type_=sa.String(255), existing_type=sa.Text(), nullable=True)
