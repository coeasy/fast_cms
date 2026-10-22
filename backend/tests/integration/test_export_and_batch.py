from __future__ import annotations

import csv
import io
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import auth


def _unwrap(response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _headers(token: str) -> dict[str, str]:
    return auth(token)


def _read_csv(response) -> list[list[str]]:
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    text = response.text.lstrip("\ufeff")
    return list(csv.reader(io.StringIO(text)))


def test_export_users_returns_csv_with_whitelisted_columns(
    client: TestClient, admin_token: str
) -> None:
    response = client.get("/api/v1/export/users", headers=_headers(admin_token))
    assert response.status_code == 200
    rows = _read_csv(response)
    assert rows[0][0] == "ID"
    assert "用户名" in rows[0]
    assert "是否封禁" in rows[0]
    # 列白名单：密码哈希之类的字段绝不允许出现在导出里
    assert not any("password" in cell.lower() for cell in rows[0])
    assert len(rows) >= 2  # 至少有 admin 一行


def test_export_rows_header_reports_count(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/v1/export/roles", headers=_headers(admin_token))
    assert response.status_code == 200
    assert int(response.headers["X-Export-Rows"]) >= 1
    assert response.headers["X-Export-Truncated"] == "0"


def test_export_limit_is_truncated_and_flagged(client: TestClient, admin_token: str) -> None:
    """行数上限必须在响应头里显式暴露，否则调用方以为导全了。"""
    headers = _headers(admin_token)
    _unwrap(
        client.post(
            "/api/v1/system/users",
            headers=headers,
            json={
                "username": "export_target",
                "password": "Export12345",
                "nickname": "导出目标",
                "must_change_password": False,
            },
        )
    )
    response = client.get(
        "/api/v1/export/users", headers=headers, params={"limit": 1}
    )
    assert response.status_code == 200
    assert response.headers["X-Export-Truncated"] == "1"
    assert int(response.headers["X-Export-Rows"]) == 1
    assert len(_read_csv(response)) == 2  # 表头 + 1 行


def test_export_writes_audit_log(client: TestClient, admin_token: str) -> None:
    """导出敏感数据必须留痕：谁导出了什么。"""
    client.get("/api/v1/export/users", headers=_headers(admin_token))
    logs = _unwrap(
        client.get(
            "/api/v1/monitor/operation-logs",
            headers=_headers(admin_token),
            params={"module": "users", "keyword": "export"},
        )
    )
    assert any(item["action"] == "users.export" for item in logs["items"]), logs["items"][:3]


def test_export_requires_its_own_permission(client: TestClient, admin_token: str) -> None:
    """能导出用户的人不该自动获得导出其他对象的能力。"""
    response = client.get("/api/v1/export/dicts")
    assert response.status_code == 401
    bad = client.get("/api/v1/export/dicts", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_export_rejects_unknown_spec(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/v1/export/nope", headers=_headers(admin_token))
    assert response.status_code == 404


def test_batch_disable_and_enable_users(client: TestClient, admin_token: str) -> None:
    headers = _headers(admin_token)
    created = _unwrap(
        client.post(
            "/api/v1/system/users",
            headers=headers,
            json={
                "username": "batch_target",
                "password": "Batch12345",
                "nickname": "批量目标",
                "must_change_password": False,
            },
        )
    )
    result = _unwrap(
        client.post(
            "/api/v1/system/users/batch",
            headers=headers,
            json={"action": "disable", "ids": [created["id"]]},
        )
    )
    assert result["succeeded"] == [created["id"]]
    assert result["failed"] == []

    enabled = _unwrap(
        client.post(
            "/api/v1/system/users/batch",
            headers=headers,
            json={"action": "enable", "ids": [created["id"]]},
        )
    )
    assert enabled["succeeded"] == [created["id"]]


def test_batch_reports_per_item_failures(client: TestClient, admin_token: str) -> None:
    """单条失败不能连带整个批次回滚。"""
    headers = _headers(admin_token)
    me = _unwrap(client.get("/api/v1/auth/me", headers=headers))
    result = _unwrap(
        client.post(
            "/api/v1/system/users/batch",
            headers=headers,
            json={"action": "delete", "ids": [999999, me["user"]["id"]]},
        )
    )
    assert result["succeeded"] == []
    assert len(result["failed"]) == 2
    codes = {item["id"]: item["code"] for item in result["failed"]}
    assert codes[999999] == "SYSTEM_USER_NOT_FOUND"
    assert codes[me["user"]["id"]] == "SYSTEM_SELF_DELETE"


def test_batch_rejects_empty_and_oversized(client: TestClient, admin_token: str) -> None:
    headers = _headers(admin_token)
    empty = client.post(
        "/api/v1/system/users/batch", headers=headers, json={"action": "disable", "ids": []}
    )
    assert empty.status_code == 422

    oversized = client.post(
        "/api/v1/system/users/batch",
        headers=headers,
        json={"action": "disable", "ids": list(range(1, 300))},
    )
    assert oversized.status_code == 422


def test_batch_rejects_unknown_action(client: TestClient, admin_token: str) -> None:
    response = client.post(
        "/api/v1/system/users/batch",
        headers=_headers(admin_token),
        json={"action": "explode", "ids": [1]},
    )
    assert response.status_code == 422


def test_batch_delete_logs(client: TestClient, admin_token: str) -> None:
    headers = _headers(admin_token)
    result = _unwrap(
        client.request(
            "DELETE",
            "/api/v1/monitor/operation-logs/batch",
            headers=headers,
            json={"ids": [999999]},
        )
    )
    assert result["succeeded"] == []
    assert result["failed"][0]["code"] == "AUDIT_LOG_NOT_FOUND"
