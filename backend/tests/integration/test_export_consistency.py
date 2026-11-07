"""P0-4 / P0-5 / P0-6：导出上限单源、数据范围策略声明、审计 CSV 统一与事务一致。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core import export as core_export
from app.modules.ops.audit import service as audit_service
from app.modules.ops.export import service as export_service
from tests.conftest import auth


def test_export_limit_is_single_source() -> None:
    """业务导出与审计导出必须引用 core/export.MAX_EXPORT_ROWS，不允许独立常量。"""
    assert export_service.EXPORT_LIMIT == core_export.MAX_EXPORT_ROWS
    assert audit_service.MAX_EXPORT_ROWS == core_export.MAX_EXPORT_ROWS
    assert core_export.MAX_EXPORT_ROWS == 10000


def test_export_router_has_no_hardcoded_large_limit() -> None:
    """路由参数上限必须引用 MAX_EXPORT_ROWS，不能出现 50000 之类的第二套口径。"""
    import inspect

    from app.modules.ops.export import router as export_router

    source = inspect.getsource(export_router)
    assert "le=MAX_EXPORT_ROWS" in source
    assert "50000" not in source


def test_csv_response_reports_limit_header() -> None:
    response, count, truncated = core_export.csv_response(
        ["a"], (["x"],), "t.csv", limit=5
    )
    assert count == 1
    assert truncated is False
    assert response.headers["X-Export-Rows"] == "1"
    assert response.headers["X-Export-Limit"] == "5"
    assert response.headers["X-Export-Truncated"] == "0"


def test_csv_response_truncation_flags() -> None:
    rows = (["1"], ["2"], ["3"])
    response, count, truncated = core_export.csv_response(["a"], rows, "t.csv", limit=2)
    assert count == 2
    assert truncated is True
    assert response.headers["X-Export-Limit"] == "2"
    assert response.headers["X-Export-Truncated"] == "1"


def test_every_export_spec_declares_scope_policy() -> None:
    """每个导出对象都必须显式声明数据范围策略：user 或 global（显式白名单）。"""
    assert export_service.SPECS, "导出规格不应为空"
    for key, spec in export_service.SPECS.items():
        assert spec.scope_policy in {"user", "global"}, f"{key} 缺少 scope_policy 声明"
    assert export_service.SPECS["users"].scope_policy == "user"
    for key, spec in export_service.SPECS.items():
        if key != "users":
            assert spec.scope_policy == "global", (
                f"{key} 是系统全局资源，必须显式声明 global（权限码已限制可见性）"
            )


def test_audit_export_reuses_unified_csv_component() -> None:
    """审计导出必须复用 core/export.csv_response，不再自建 CSV 拼装。"""
    assert audit_service.csv_response is core_export.csv_response
    assert not hasattr(audit_service, "_csv_response")


async def test_audit_export_commits_exactly_once() -> None:
    """P0-6：导出审计一次操作只提交一次，不出现连续两次 commit。"""
    from unittest.mock import Mock

    db = Mock()
    db.add = Mock()  # type: ignore[method-assign]
    db.commit = AsyncMock()  # type: ignore[method-assign]

    context = type("Ctx", (), {"ip": "1.2.3.4", "user_agent": "unit-test"})()
    with patch("app.modules.ops.audit.service.current_context", return_value=context), patch(
        "app.modules.ops.audit.service.get_request_id", return_value="req-123"
    ):
        await audit_service.write_export_audit(
            db,
            user_id=1,
            username="admin",
            module="users",
            action="users.export",
            path="/export/users",
            detail="rows=2",
        )

    assert db.add.call_count == 1
    assert db.commit.call_count == 1
    logged: Any = db.add.call_args[0][0]
    assert logged.action == "users.export"
    assert logged.request_id == "req-123"


def test_audit_export_sets_limit_header(client: TestClient, admin_token: str) -> None:
    response = client.get(
        "/api/v1/monitor/login-logs/export", headers=auth(admin_token)
    )
    assert response.status_code == 200, response.text
    assert response.headers["X-Export-Limit"] == str(core_export.MAX_EXPORT_ROWS)
    assert response.headers["X-Export-Truncated"] in {"0", "1"}


def test_business_export_sets_limit_header(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/v1/export/users", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert response.headers["X-Export-Limit"] == str(core_export.MAX_EXPORT_ROWS)
