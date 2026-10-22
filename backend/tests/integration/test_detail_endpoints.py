"""详情类接口的回归测试。

前端页面主要使用列表接口，这些单条详情接口是 REST 契约的一部分（文档中已规划），
必须保持可用、权限正确且不泄露内部字段，因此在此集中覆盖。
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.conftest import TEST_DB, auth, login


def _create_viewer(client: TestClient, headers: dict[str, str], username: str) -> tuple[int, str]:
    """创建一个没有任何业务权限的普通用户，返回 (用户 ID, token)。"""
    created = client.post(
        "/api/v1/system/users",
        headers=headers,
        json={
            "username": username,
            "password": "Viewer12345",
            "nickname": "越权探测",
            "must_change_password": False,
        },
    )
    assert created.status_code == 200, created.text
    return created.json()["data"]["id"], login(client, username, "Viewer12345")["access_token"]


def test_user_detail_returns_roles_and_rejects_anonymous_probing(
    client: TestClient, admin_token: str
) -> None:
    headers = auth(admin_token)
    user_id, viewer_token = _create_viewer(client, headers, "detail_probe_user")

    detail = client.get(f"/api/v1/system/users/{user_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["id"] == user_id
    assert data["username"] == "detail_probe_user"
    assert [role["code"] for role in data["roles"]] == []
    assert "password" not in data and "password_hash" not in detail.text

    # 管理员可读取，但普通用户即使拿到 token 也会被 admin 控制台拦截
    forbidden = client.get(f"/api/v1/system/users/{user_id}", headers=auth(viewer_token))
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "AUTH_FORBIDDEN"

    missing = client.get("/api/v1/system/users/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "SYSTEM_USER_NOT_FOUND"

    assert client.delete(f"/api/v1/system/users/{user_id}", headers=headers).status_code == 200


def test_role_detail_includes_user_count_and_permissions(
    client: TestClient, admin_token: str
) -> None:
    headers = auth(admin_token)
    roles = client.get("/api/v1/system/roles", headers=headers).json()["data"]
    auditor = next(role for role in roles if role["code"] == "auditor")

    user_id, _ = _create_viewer(client, headers, "detail_scope_user")
    assigned = client.put(
        f"/api/v1/system/users/{user_id}/roles",
        headers=headers,
        json={"role_ids": [auditor["id"]]},
    )
    assert assigned.status_code == 200, assigned.text

    detail = client.get(f"/api/v1/system/roles/{auditor['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["code"] == "auditor"
    assert data["user_count"] >= 1
    assert "permission_codes" in data and "dept_ids" in data

    missing = client.get("/api/v1/system/roles/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "SYSTEM_ROLE_NOT_FOUND"

    assert client.delete(f"/api/v1/system/users/{user_id}", headers=headers).status_code == 200


def test_theme_and_dict_type_detail(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    themes = client.get("/api/v1/system/themes", headers=headers).json()["data"]
    assert themes

    theme = client.get(f"/api/v1/system/themes/{themes[0]['id']}", headers=headers)
    assert theme.status_code == 200, theme.text
    assert theme.json()["data"]["code"] == themes[0]["code"]
    assert "config" in theme.json()["data"]

    missing_theme = client.get("/api/v1/system/themes/999999", headers=headers)
    assert missing_theme.status_code == 404
    assert missing_theme.json()["code"] == "THEME_NOT_FOUND"

    dict_types = client.get("/api/v1/system/dict-types", headers=headers).json()["data"]
    if dict_types:
        detail = client.get(f"/api/v1/system/dict-types/{dict_types[0]['id']}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert "items" in detail.json()["data"]
    missing_dict = client.get("/api/v1/system/dict-types/999999", headers=headers)
    assert missing_dict.status_code == 404
    assert missing_dict.json()["code"] == "CONFIG_DICT_NOT_FOUND"


def test_operation_log_detail_exposes_business_summary(
    client: TestClient, admin_token: str
) -> None:
    headers = auth(admin_token)
    created = client.post(
        "/api/v1/content/notices",
        headers=headers,
        json={"title": "详情接口审计锚点", "content": "用于验证操作日志详情"},
    )
    assert created.status_code == 200, created.text
    notice_id = created.json()["data"]["id"]

    listed = client.get(
        "/api/v1/monitor/operation-logs", headers=headers, params={"module": "content", "page_size": 50}
    )
    assert listed.status_code == 200, listed.text
    items = [
        item
        for item in listed.json()["data"]["items"]
        if item["method"] == "POST" and item["path"].endswith("/content/notices")
    ]
    assert items, listed.text
    target = max(items, key=lambda item: item["id"])
    # 列表响应刻意不带 detail，避免批量拉取时泄露请求体
    assert "detail" not in target

    detail = client.get(f"/api/v1/monitor/operation-logs/{target['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["detail"] is not None
    assert "详情接口审计锚点" in body["detail"]

    missing = client.get("/api/v1/monitor/operation-logs/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "AUDIT_LOG_NOT_FOUND"

    assert client.delete(f"/api/v1/content/notices/{notice_id}", headers=headers).status_code == 200


def test_login_log_detail_and_data_scope_visibility(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    login(client, "admin", "Owner123456")

    listed = client.get("/api/v1/monitor/login-logs", headers=headers, params={"page_size": 20})
    assert listed.status_code == 200, listed.text
    items = [item for item in listed.json()["data"]["items"] if item["username"] == "admin"]
    assert items, listed.text

    detail = client.get(f"/api/v1/monitor/login-logs/{items[0]['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["username"] == "admin"

    missing = client.get("/api/v1/monitor/login-logs/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "AUDIT_LOG_NOT_FOUND"


def test_notice_published_detail_rejects_draft(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = client.post(
        "/api/v1/content/notices",
        headers=headers,
        json={"title": "草稿不可见", "content": "草稿阶段用户端不应读到", "status": "draft"},
    )
    assert created.status_code == 200, created.text
    notice_id = created.json()["data"]["id"]

    draft = client.get(f"/api/v1/content/notices/{notice_id}/published", headers=headers)
    assert draft.status_code == 404
    assert draft.json()["code"] == "CONTENT_NOTICE_NOT_FOUND"

    # 管理端详情可以看到草稿，两端语义不同
    admin_detail = client.get(f"/api/v1/content/notices/{notice_id}", headers=headers)
    assert admin_detail.status_code == 200, admin_detail.text
    assert admin_detail.json()["data"]["status"] == "draft"

    assert client.post(f"/api/v1/content/notices/{notice_id}/publish", headers=headers).status_code == 200
    published = client.get(f"/api/v1/content/notices/{notice_id}/published", headers=headers)
    assert published.status_code == 200, published.text
    assert published.json()["data"]["title"] == "草稿不可见"
    assert published.json()["data"]["read"] is False

    assert client.delete(f"/api/v1/content/notices/{notice_id}", headers=headers).status_code == 200


def test_notice_unpublish_hides_from_users(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = client.post(
        "/api/v1/content/notices",
        headers=headers,
        json={"title": "下线公告", "content": "下线后用户端不可见", "status": "published"},
    )
    assert created.status_code == 200, created.text
    notice_id = created.json()["data"]["id"]

    listed = client.get("/api/v1/content/notices/published", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == notice_id for item in listed.json()["data"]["items"])

    offline = client.post(f"/api/v1/content/notices/{notice_id}/unpublish", headers=headers)
    assert offline.status_code == 200, offline.text
    assert offline.json()["data"]["status"] == "offline"

    hidden = client.get(f"/api/v1/content/notices/{notice_id}/published", headers=headers)
    assert hidden.status_code == 404
    listed = client.get("/api/v1/content/notices/published", headers=headers)
    assert all(item["id"] != notice_id for item in listed.json()["data"]["items"])

    # 重复下线是无效状态流转，明确报错而不是静默成功
    again = client.post(f"/api/v1/content/notices/{notice_id}/unpublish", headers=headers)
    assert again.status_code == 400
    assert again.json()["code"] == "CONTENT_NOTICE_NOT_PUBLISHED"

    republished = client.post(f"/api/v1/content/notices/{notice_id}/publish", headers=headers)
    assert republished.status_code == 200, republished.text
    back = client.get(f"/api/v1/content/notices/{notice_id}/published", headers=headers)
    assert back.status_code == 200, back.text

    assert client.delete(f"/api/v1/content/notices/{notice_id}", headers=headers).status_code == 200


def test_codegen_columns_detail(client: TestClient, admin_token: str) -> None:
    with sqlite3.connect(TEST_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS demo_column_probe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(64) NOT NULL, "
            "amount INTEGER)"
        )
        connection.commit()

    headers = auth(admin_token)
    columns = client.get("/api/v1/tool/codegen/tables/demo_column_probe/columns", headers=headers)
    assert columns.status_code == 200, columns.text
    data = columns.json()["data"]
    assert [item["name"] for item in data] == ["id", "name", "amount"]
    assert data[0]["primary_key"] is True
    assert data[1]["nullable"] is False

    unknown = client.get("/api/v1/tool/codegen/tables/demo_missing_table/columns", headers=headers)
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "CODEGEN_TABLE_NOT_FOUND"

    # 表名先经过标识符校验，非法输入不会被当成“表不存在”
    invalid = client.get("/api/v1/tool/codegen/tables/demo;drop/columns", headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "CODEGEN_TABLE_INVALID"


def test_file_meta_detail_hides_internal_path(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    upload = client.post(
        "/api/v1/tool/files/upload",
        headers=headers,
        files={"file": ("meta.txt", b"fastcms meta", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    file_id = upload.json()["data"]["id"]

    detail = client.get(f"/api/v1/tool/files/{file_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["original_name"] == "meta.txt"
    assert data["size"] == len(b"fastcms meta")
    assert data["download_url"]
    assert "storage_path" not in data and "stored_name" not in data

    missing = client.get("/api/v1/tool/files/999999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "TOOL_FILE_NOT_FOUND"

    assert client.delete(f"/api/v1/tool/files/{file_id}", headers=headers).status_code == 200
