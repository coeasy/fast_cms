from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import delete, select

from app.core.authz import DataScope
from app.modules.audit.models import LoginLog
from app.modules.audit.service import _restrict_to_scope

CTX = SimpleNamespace(is_superuser=False, user=SimpleNamespace(id=7))  # type: ignore[arg-type]
SCOPE = DataScope(dept_ids={3})


def _sql(statement) -> str:  # type: ignore[no-untyped-def]
    return str(statement.compile())


def test_scoped_read_still_shares_orphan_rows() -> None:
    sql = _sql(_restrict_to_scope(select(LoginLog), CTX, SCOPE, LoginLog.user_id))
    assert "IS NULL" in sql
    assert "IN (SELECT" in sql


def test_scoped_cleanup_never_touches_orphan_rows() -> None:
    sql = _sql(
        _restrict_to_scope(
            delete(LoginLog).where(LoginLog.created_at < "2026-01-01"),
            CTX,
            SCOPE,
            LoginLog.user_id,
            include_orphan=False,
        )
    )
    # 爆破产生的无主日志不属于任何部门，部门管理员不能删除
    assert "IS NULL" not in sql
    assert "IN (SELECT" in sql


def test_full_scope_admin_is_unfiltered() -> None:
    sql = _sql(_restrict_to_scope(delete(LoginLog), CTX, None, LoginLog.user_id, include_orphan=False))
    assert "user_id" not in sql
    assert "SELECT" not in sql
