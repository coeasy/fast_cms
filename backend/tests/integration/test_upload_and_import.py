from __future__ import annotations

import base64
import csv
import io
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import auth

# 1x1 透明 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8"
    "DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
HTML = b"<!DOCTYPE html><html><body><script>alert(1)</script></body></html>"


def _unwrap(response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _headers(token: str) -> dict[str, str]:
    return auth(token)


def _csv_bytes(rows: list[list[str]], *, bom: bool = False) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    text = buffer.getvalue()
    return ("\ufeff" + text).encode("utf-8") if bom else text.encode("utf-8")


# ---------------------------------------------------------------- 上传加固


def test_upload_rejects_executable_extension(client: TestClient, admin_token: str) -> None:
    response = client.post(
        "/api/v1/tool/files/upload",
        headers=_headers(admin_token),
        files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "TOOL_FILE_TYPE_FORBIDDEN"


def test_upload_rejects_html_disguised_as_png(client: TestClient, admin_token: str) -> None:
    """.png 里其实是 HTML：如果被内联展示就是存储型 XSS。"""
    response = client.post(
        "/api/v1/tool/files/upload",
        headers=_headers(admin_token),
        files={"file": ("x.png", HTML, "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "TOOL_FILE_TYPE_MISMATCH"


def test_upload_rejects_binary_smuggled_as_text(client: TestClient, admin_token: str) -> None:
    response = client.post(
        "/api/v1/tool/files/upload",
        headers=_headers(admin_token),
        files={"file": ("smuggled.txt", PNG, "text/plain")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "TOOL_FILE_TYPE_MISMATCH"


def test_upload_rejects_file_without_extension(client: TestClient, admin_token: str) -> None:
    response = client.post(
        "/api/v1/tool/files/upload",
        headers=_headers(admin_token),
        files={"file": ("README", b"plain text", "text/plain")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "TOOL_FILE_NO_EXTENSION"


def test_upload_stores_server_sniffed_type_not_client_claim(
    client: TestClient, admin_token: str
) -> None:
    """客户端声明 text/plain 也不该改变服务端对真实内容的判定。"""
    uploaded = _unwrap(
        client.post(
            "/api/v1/tool/files/upload",
            headers=_headers(admin_token),
            files={"file": ("logo.png", PNG, "text/plain")},
        )
    )
    assert uploaded["content_type"] == "image/png"
    assert uploaded["sha256"]


def test_upload_deduplicates_identical_content(client: TestClient, admin_token: str) -> None:
    """同一上传者重复上传同一内容只保留一条记录。"""
    headers = _headers(admin_token)
    first = _unwrap(
        client.post(
            "/api/v1/tool/files/upload",
            headers=headers,
            files={"file": ("dup.txt", b"same bytes here", "text/plain")},
        )
    )
    second = _unwrap(
        client.post(
            "/api/v1/tool/files/upload",
            headers=headers,
            files={"file": ("another-name.txt", b"same bytes here", "text/plain")},
        )
    )
    assert second["id"] == first["id"]
    assert second["sha256"] == first["sha256"]


def test_download_carries_nosniff(client: TestClient, admin_token: str) -> None:
    """没有 nosniff，浏览器会忽略 media_type 自己猜类型。"""
    headers = _headers(admin_token)
    uploaded = _unwrap(
        client.post(
            "/api/v1/tool/files/upload",
            headers=headers,
            files={"file": ("dl.txt", b"download me", "text/plain")},
        )
    )
    response = client.get(
        f"/api/v1/tool/files/{uploaded['id']}/download", headers=headers
    )
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content == b"download me"


# ---------------------------------------------------------------- 用户导入


def test_import_users_creates_rows_and_reports_failures(
    client: TestClient, admin_token: str
) -> None:
    """第 2 行密码太弱不该让第 1 行白导入。"""
    headers = _headers(admin_token)
    rows = [
        ["用户名", "密码", "昵称"],
        ["import_ok", "StrongPass123", "导入成功"],
        ["import_weak", "123", "密码太弱"],
    ]
    result = _unwrap(
        client.post(
            "/api/v1/system/users/import",
            headers=headers,
            files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
        )
    )
    assert result["total"] == 2
    assert [item["username"] for item in result["succeeded"]] == ["import_ok"]
    assert result["succeeded"][0]["row"] == 2  # 表头占第 1 行
    assert len(result["failed"]) == 1
    assert result["failed"][0]["row"] == 3
    assert result["failed"][0]["username"] == "import_weak"

    listed = _unwrap(
        client.get(
            "/api/v1/system/users",
            headers=headers,
            params={"keyword": "import_ok"},
        )
    )
    assert listed["total"] == 1


def test_import_handles_bom_and_chinese_headers(client: TestClient, admin_token: str) -> None:
    """Excel 另存的 UTF-8 CSV 带 BOM，不剥掉第一个列名会读不到。"""
    headers = _headers(admin_token)
    rows = [
        ["用户名", "密码", "昵称", "部门ID", "角色ID"],
        ["import_bom", "StrongPass123", "BOM 用户", "", ""],
    ]
    result = _unwrap(
        client.post(
            "/api/v1/system/users/import",
            headers=headers,
            files={"file": ("users.csv", _csv_bytes(rows, bom=True), "text/csv")},
        )
    )
    assert result["failed"] == []
    assert result["succeeded"][0]["username"] == "import_bom"


def test_import_dry_run_writes_nothing(client: TestClient, admin_token: str) -> None:
    """预检必须真的走一遍写入路径，否则发现不了用户名撞车。"""
    headers = _headers(admin_token)
    rows = [
        ["username", "password", "nickname"],
        ["dry_run_user", "StrongPass123", "预检用户"],
    ]
    result = _unwrap(
        client.post(
            "/api/v1/system/users/import",
            headers=headers,
            params={"dry_run": "true"},
            files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
        )
    )
    assert result["dry_run"] is True
    assert result["failed"] == []
    assert len(result["succeeded"]) == 1

    listed = _unwrap(
        client.get(
            "/api/v1/system/users",
            headers=headers,
            params={"keyword": "dry_run_user"},
        )
    )
    assert listed["total"] == 0, "预检不应该落库"


def test_import_dry_run_catches_duplicate_username(client: TestClient, admin_token: str) -> None:
    headers = _headers(admin_token)
    rows = [
        ["username", "password", "nickname"],
        ["admin", "StrongPass123", "重名"],
    ]
    result = _unwrap(
        client.post(
            "/api/v1/system/users/import",
            headers=headers,
            params={"dry_run": "true"},
            files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
        )
    )
    assert len(result["failed"]) == 1
    assert result["failed"][0]["code"] == "SYSTEM_USERNAME_EXISTS"


def test_import_rejects_unknown_column(client: TestClient, admin_token: str) -> None:
    """列名打错时静默丢掉该列，比报错危险得多。"""
    headers = _headers(admin_token)
    rows = [
        ["usernme", "password", "nickname"],  # username 少个 a
        ["someone", "StrongPass123", "某人"],
    ]
    response = client.post(
        "/api/v1/system/users/import",
        headers=headers,
        files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "IMPORT_COLUMN_UNKNOWN"
    assert "usernme" in response.json()["message"]


def test_import_rejects_missing_required_column(client: TestClient, admin_token: str) -> None:
    headers = _headers(admin_token)
    rows = [["username", "nickname"], ["someone", "某人"]]
    response = client.post(
        "/api/v1/system/users/import",
        headers=headers,
        files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "IMPORT_COLUMN_MISSING"


def test_import_rejects_too_many_rows(client: TestClient, admin_token: str) -> None:
    headers = _headers(admin_token)
    rows = [["username", "password", "nickname"]]
    rows += [[f"bulk_{i}", "StrongPass123", f"批量{i}"] for i in range(501)]
    response = client.post(
        "/api/v1/system/users/import",
        headers=headers,
        files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "IMPORT_TOO_MANY_ROWS"


def test_import_requires_authentication(client: TestClient) -> None:
    rows = [["username", "password", "nickname"], ["x", "StrongPass123", "x"]]
    response = client.post(
        "/api/v1/system/users/import",
        files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
    )
    assert response.status_code == 401


def test_import_writes_audit_log(client: TestClient, admin_token: str) -> None:
    headers = _headers(admin_token)
    rows = [
        ["username", "password", "nickname"],
        ["import_audited", "StrongPass123", "留痕用户"],
    ]
    _unwrap(
        client.post(
            "/api/v1/system/users/import",
            headers=headers,
            files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
        )
    )
    logs = _unwrap(
        client.get(
            "/api/v1/monitor/operation-logs",
            headers=headers,
            params={"keyword": "import"},
        )
    )
    assert any(item["action"] == "user.import" for item in logs["items"]), logs["items"][:3]
