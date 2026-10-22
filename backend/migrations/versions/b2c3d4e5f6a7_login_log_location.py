"""login log ip location

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-22 09:55:00.000000

登录日志补一列 IP 归属地。归属地在登录成功的那一刻解析并落库，
之后更换 xdb 数据也不会改写历史记录——审计看到的过去必须是事实，
不能因为升级了库就让上周的登录记录「搬家」。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sys_login_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("location", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sys_login_log", schema=None) as batch_op:
        batch_op.drop_column("location")
