from __future__ import annotations

import io
import sqlite3
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TEST_DB, auth, login


def test_public_endpoints(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz")
    assert ready.status_code == 200, ready.text
    meta = client.get("/api/v1/meta/version").json()
    assert meta["code"] == "OK"
    assert meta["data"]["version"]
    # 匿名接口不得回传环境指纹，运行时信息只走有权限的 /monitor/server
    assert set(meta["data"]) == {"name", "version", "timezone", "server_time"}
    public = client.get("/api/v1/system/public-config").json()
    assert public["data"]["system.title"]


def test_unknown_route_returns_json_404(client: TestClient) -> None:
    response = client.get("/api/v1/schedule/logs")
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["code"] == "HTTP_404"
    assert body["message"] == "请求的资源不存在"


def test_login_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/system/users")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_UNAUTHORIZED"


def test_login_wrong_password_and_lockout_counter(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-pass1"})
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_INVALID_CREDENTIALS"


def test_me_after_password_change(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/v1/auth/me", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["user"]["username"] == "admin"
    assert data["is_superuser"] is True
    assert data["is_admin"] is True
    assert "system:user:create" in data["permissions"]
    assert {menu["path"] for menu in data["menus"]} >= {"/dashboard", "/system", "/monitor"}


def test_system_rbac_flow(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    roles = client.get("/api/v1/system/roles", headers=headers).json()["data"]
    auditor = next(role for role in roles if role["code"] == "auditor")

    created = client.post(
        "/api/v1/system/users",
        headers=headers,
        json={
            "username": "tester",
            "password": "Zebra12345",
            "nickname": "测试用户",
            "must_change_password": False,
        },
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["data"]["id"]

    denied = client.get("/api/v1/system/users", headers=auth(login(client, "tester", "Zebra12345")["access_token"]))
    assert denied.status_code == 403

    assigned = client.put(
        f"/api/v1/system/users/{user_id}/roles",
        headers=headers,
        json={"role_ids": [auditor["id"]]},
    )
    assert assigned.status_code == 200, assigned.text

    tester_token = login(client, "tester", "Zebra12345")["access_token"]
    assert client.get("/api/v1/system/users", headers=auth(tester_token)).status_code == 200
    forbidden_create = client.post(
        "/api/v1/system/users",
        headers=auth(tester_token),
        json={"username": "notallowed", "password": "Whatever123", "nickname": "x"},
    )
    assert forbidden_create.status_code == 403

    disabled = client.patch(
        f"/api/v1/system/users/{user_id}/status", headers=headers, json={"status": "disabled"}
    )
    assert disabled.status_code == 200, disabled.text
    assert client.get("/api/v1/system/users", headers=auth(tester_token)).status_code == 401

    deleted = client.delete(f"/api/v1/system/users/{user_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text


def test_admin_console_requires_explicit_access(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    role = client.post(
        "/api/v1/system/roles",
        headers=headers,
        json={"name": "业务查看员", "code": "business_viewer", "data_scope": "all"},
    )
    assert role.status_code == 200, role.text
    role_id = role.json()["data"]["id"]

    permissions = client.put(
        f"/api/v1/system/roles/{role_id}/permissions",
        headers=headers,
        json={"permission_codes": ["dashboard:view", "system:user:list", "system:notice:list"]},
    )
    assert permissions.status_code == 200, permissions.text

    created = client.post(
        "/api/v1/system/users",
        headers=headers,
        json={
            "username": "business_user",
            "password": "Business12345",
            "nickname": "业务用户",
            "must_change_password": False,
            "role_ids": [role_id],
        },
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["data"]["id"]

    token = login(client, "business_user", "Business12345")["access_token"]
    me = client.get("/api/v1/auth/me", headers=auth(token)).json()["data"]
    assert me["is_admin"] is False
    assert me["menus"] == []
    assert "system:user:list" in me["permissions"]

    denied = client.get("/api/v1/system/users", headers=auth(token))
    assert denied.status_code == 403
    assert denied.json()["message"] == "仅管理员可访问管理后台"
    assert client.get("/api/v1/dashboard/summary", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/content/notices", headers=auth(token)).status_code == 403
    assert client.put(
        "/api/v1/auth/profile", headers=auth(token), json={"nickname": me["user"]["nickname"]}
    ).status_code == 200
    assert client.get("/api/v1/content/notices/published", headers=auth(token)).status_code == 200

    granted = client.put(
        f"/api/v1/system/roles/{role_id}/permissions",
        headers=headers,
        json={
            "permission_codes": [
                "system:admin:access",
                "dashboard:view",
                "system:user:list",
                "system:notice:list",
            ]
        },
    )
    assert granted.status_code == 200, granted.text
    me = client.get("/api/v1/auth/me", headers=auth(token)).json()["data"]
    assert me["is_admin"] is True
    assert {menu["path"] for menu in me["menus"]} >= {"/dashboard", "/system"}
    assert client.get("/api/v1/system/users", headers=auth(token)).status_code == 200
    assert client.get("/api/v1/content/notices", headers=auth(token)).status_code == 200

    deleted = client.delete(f"/api/v1/system/users/{user_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    removed = client.delete(f"/api/v1/system/roles/{role_id}", headers=headers)
    assert removed.status_code == 200, removed.text


def test_departments_dictionaries_and_configs(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    dept = client.post(
        "/api/v1/system/depts", headers=headers, json={"name": "研发部", "leader": "张三", "sort": 2}
    )
    assert dept.status_code == 200, dept.text
    dept_id = dept.json()["data"]["id"]
    child = client.post(
        "/api/v1/system/depts",
        headers=headers,
        json={"name": "平台组", "parent_id": dept_id, "sort": 1},
    )
    assert child.status_code == 200, child.text
    tree = client.get("/api/v1/system/depts", headers=headers).json()["data"]
    assert any(item["id"] == dept_id and item["children"] for item in tree)

    dictionary = client.get("/api/v1/system/dicts/user_status", headers=headers)
    assert dictionary.status_code == 200, dictionary.text
    assert any(item["value"] == "active" for item in dictionary.json()["data"]["items"])

    configs = client.get("/api/v1/system/configs", headers=headers)
    assert configs.status_code == 200, configs.text

    deleted = client.delete(f"/api/v1/system/depts/{dept_id}", headers=headers)
    assert deleted.status_code == 409


def test_monitor_online_and_logs(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    online = client.get("/api/v1/monitor/online", headers=headers)
    assert online.status_code == 200, online.text
    assert any(item["username"] == "admin" for item in online.json()["data"])

    server = client.get("/api/v1/monitor/server", headers=headers)
    assert server.status_code == 200, server.text
    assert server.json()["data"]["cpu"]["cores"] >= 1

    login_logs = client.get("/api/v1/monitor/login-logs", headers=headers, params={"status": "success"})
    assert login_logs.status_code == 200, login_logs.text
    assert login_logs.json()["data"]["total"] >= 1

    export = client.get("/api/v1/monitor/login-logs/export", headers=headers)
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")

    logs = client.get("/api/v1/monitor/operation-logs", headers=headers)
    assert logs.status_code == 200, logs.text


def test_schedule_jobs_and_manual_trigger(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    definitions = client.get("/api/v1/schedule/jobs/definitions", headers=headers)
    assert definitions.status_code == 200, definitions.text
    assert {item["code"] for item in definitions.json()["data"]} >= {"system.cleanup_sessions"}

    jobs = client.get("/api/v1/schedule/jobs", headers=headers)
    assert jobs.status_code == 200, jobs.text
    heartbeat = next(item for item in jobs.json()["data"]["items"] if item["code"] == "system.heartbeat")
    assert heartbeat["status"] == "paused"

    created = client.post(
        "/api/v1/schedule/jobs",
        headers=headers,
        json={
            "name": "心跳任务副本",
            "code": "system.heartbeat",
            "trigger_type": "interval",
            "interval_seconds": 120,
            "timezone": "Asia/Shanghai",
            "args_json": {"message": "copy"},
            "status": "paused",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["code"] == "system.heartbeat"
    deleted = client.delete(f"/api/v1/schedule/jobs/{created.json()['data']['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    triggered = client.post(f"/api/v1/schedule/jobs/{heartbeat['id']}/trigger", headers=headers)
    assert triggered.status_code == 200, triggered.text

    log = None
    for _ in range(30):
        logs = client.get(
            "/api/v1/schedule/job-logs", headers=headers, params={"job_id": heartbeat["id"]}
        ).json()["data"]
        if logs["items"]:
            log = logs["items"][0]
            break
        time.sleep(0.1)
    assert log is not None
    assert log["status"] in {"running", "success"}


def test_python_job_concurrent_trigger_is_deduplicated(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = client.post(
        "/api/v1/schedule/jobs",
        headers=headers,
        json={
            "name": "并发去重验证",
            "code": "demo.dedup_lock",
            "job_type": "python",
            "script_code": "import time\n\nprint('start', flush=True)\ntime.sleep(2)\nprint('done', flush=True)",
            "trigger_type": "interval",
            "interval_seconds": 3600,
            "timezone": "Asia/Shanghai",
            "args_json": {},
            "status": "paused",
            "timeout_seconds": 30,
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["data"]["id"]

    assert client.post(f"/api/v1/schedule/jobs/{job_id}/trigger", headers=headers).status_code == 200
    time.sleep(0.3)
    assert client.post(f"/api/v1/schedule/jobs/{job_id}/trigger", headers=headers).status_code == 200

    finished: list[dict] = []
    for _ in range(120):
        items = client.get(
            "/api/v1/schedule/job-logs", headers=headers, params={"job_id": job_id}
        ).json()["data"]["items"]
        finished = [item for item in items if item["status"] != "running"]
        if len(finished) >= 2:
            break
        time.sleep(0.1)

    client.delete(f"/api/v1/schedule/jobs/{job_id}", headers=headers)

    statuses = sorted(item["status"] for item in finished)
    assert statuses == ["skipped", "success"], statuses
    skipped = next(item for item in finished if item["status"] == "skipped")
    detail = client.get(f"/api/v1/schedule/job-logs/{skipped['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["error"] == "已有同一任务在执行，本次跳过"


def test_notices_and_files(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = client.post(
        "/api/v1/content/notices",
        headers=headers,
        json={"title": "系统维护通知", "content": "今晚 22:00 维护"},
    )
    assert created.status_code == 200, created.text
    notice_id = created.json()["data"]["id"]
    published = client.post(f"/api/v1/content/notices/{notice_id}/publish", headers=headers)
    assert published.status_code == 200, published.text

    user_notices = client.get("/api/v1/content/notices/published", headers=headers)
    assert user_notices.status_code == 200, user_notices.text
    assert any(item["id"] == notice_id for item in user_notices.json()["data"]["items"])
    read = client.post(f"/api/v1/content/notices/{notice_id}/read", headers=headers)
    assert read.status_code == 200
    count = client.get("/api/v1/content/notices/unread-count", headers=headers)
    assert count.json()["data"]["count"] == 0

    upload = client.post(
        "/api/v1/tool/files/upload",
        headers=headers,
        files={"file": ("hello.txt", b"hello fastcms", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    file_id = upload.json()["data"]["id"]
    downloaded = client.get(f"/api/v1/tool/files/{file_id}/download", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"hello fastcms"
    listed = client.get("/api/v1/tool/files", headers=headers)
    assert listed.json()["data"]["total"] >= 1
    assert client.delete(f"/api/v1/tool/files/{file_id}", headers=headers).status_code == 200


def test_codegen_preview_and_write(client: TestClient, admin_token: str) -> None:
    with sqlite3.connect(TEST_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS demo_article ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title VARCHAR(120) NOT NULL, "
            "views INTEGER DEFAULT 0, "
            "created_at DATETIME)"
        )
        connection.commit()

    headers = auth(admin_token)
    tables = client.get("/api/v1/tool/codegen/tables", headers=headers)
    assert tables.status_code == 200, tables.text
    demo = next(item for item in tables.json()["data"] if item["table"] == "demo_article")
    assert demo["codegen_enabled"] is True
    assert demo["column_count"] == 4

    preview = client.post(
        "/api/v1/tool/codegen/preview",
        headers=headers,
        json={"table": "demo_article", "module": "demo", "resource": "article", "title": "文章"},
    )
    assert preview.status_code == 200, preview.text
    files = preview.json()["data"]["files"]
    assert {item["path"] for item in files} >= {
        "demo/article/model.py",
        "demo/article/router.py",
        "demo/article/frontend/index.js",
    }

    refused = client.post(
        "/api/v1/tool/codegen/preview",
        headers=headers,
        json={"table": "sys_user", "module": "demo", "resource": "user"},
    )
    assert refused.status_code == 422


def test_codegen_download_and_write(
    client: TestClient, admin_token: str, tmp_path: Path, monkeypatch
) -> None:
    with sqlite3.connect(TEST_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS demo_article ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title VARCHAR(120) NOT NULL, "
            "views INTEGER DEFAULT 0, "
            "created_at DATETIME)"
        )
        connection.commit()

    import app.codegen.generator as codegen_generator
    import app.codegen.router as codegen_router

    monkeypatch.setattr(codegen_generator, "GENERATED_ROOT", tmp_path)
    monkeypatch.setattr(codegen_router, "GENERATED_ROOT", tmp_path)
    headers = auth(admin_token)
    payload = {"table": "demo_article", "module": "demo", "resource": "article", "title": "文章"}

    download = client.post("/api/v1/tool/codegen/download", headers=headers, json=payload)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = set(archive.namelist())
    assert {"demo-article/router.py", "demo-article/manifest.json"} <= names

    written = client.post("/api/v1/tool/codegen/write", headers=headers, json=payload)
    assert written.status_code == 200, written.text
    target = tmp_path / "demo" / "article"
    assert (target / "router.py").exists()
    assert (target / "manifest.json").exists()

    overwrite = client.post("/api/v1/tool/codegen/write", headers=headers, json=payload)
    assert overwrite.status_code == 409
    assert overwrite.json()["code"] == "CODEGEN_TARGET_EXISTS"


def test_logout_and_refresh_rotation(client: TestClient) -> None:
    data = login(client, "admin", "Owner123456")
    assert data["must_change_password"] is False
    refresh_cookie = client.cookies.get("fastcms_refresh")
    assert refresh_cookie

    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    new_cookie = client.cookies.get("fastcms_refresh")
    assert new_cookie and new_cookie != refresh_cookie

    client.cookies.set("fastcms_refresh", refresh_cookie, path="/api/v1/auth")
    reused = client.post("/api/v1/auth/refresh")
    assert reused.status_code == 401
    assert reused.json()["code"] == "AUTH_REFRESH_REUSED"

    second = login(client, "admin", "Owner123456")
    logged_out = client.post("/api/v1/auth/logout", headers=auth(second["access_token"]))
    assert logged_out.status_code == 200
    assert client.get("/api/v1/auth/me", headers=auth(second["access_token"])).status_code == 401


def _dept_ids(nodes: list[dict]) -> set[int]:
    ids: set[int] = set()
    for node in nodes:
        ids.add(node["id"])
        ids.update(_dept_ids(node.get("children") or []))
    return ids


def test_data_scope_limits_people_visibility(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)

    def created(response) -> dict:
        assert response.status_code == 200, response.text
        return response.json()["data"]

    dept_a = created(client.post("/api/v1/system/depts", headers=headers, json={"name": "范围A部", "sort": 61}))["id"]
    dept_a_child = created(
        client.post(
            "/api/v1/system/depts", headers=headers, json={"name": "范围A子部", "parent_id": dept_a, "sort": 1}
        )
    )["id"]
    dept_b = created(client.post("/api/v1/system/depts", headers=headers, json={"name": "范围B部", "sort": 62}))["id"]

    role = created(
        client.post(
            "/api/v1/system/roles",
            headers=headers,
            json={"name": "范围A管理员", "code": "scope_a_admin", "data_scope": "dept"},
        )
    )
    role_id = role["id"]
    created(
        client.put(
            f"/api/v1/system/roles/{role_id}/permissions",
            headers=headers,
            json={
                "permission_codes": [
                    "system:admin:access",
                    "system:user:list",
                    "system:dept:list",
                    "system:dept:create",
                    "system:dept:update",
                    "monitor:online:list",
                    "monitor:online:kick",
                    "monitor:login-log:list",
                    "monitor:login-log:export",
                    "monitor:operation-log:list",
                ]
            },
        )
    )

    users = {
        username: created(
            client.post(
                "/api/v1/system/users",
                headers=headers,
                json={
                    "username": username,
                    "password": "Scope123456",
                    "nickname": nickname,
                    "must_change_password": False,
                    "dept_id": dept_id,
                    "role_ids": role_ids,
                },
            )
        )["id"]
        for username, nickname, dept_id, role_ids in (
            ("scope_a_staff", "A部员工", dept_a, []),
            ("scope_a_child", "A子部员工", dept_a_child, []),
            ("scope_b_staff", "B部员工", dept_b, []),
            ("scope_a_manager", "A部管理员", dept_a, [role_id]),
        )
    }

    scoped = auth(login(client, "scope_a_manager", "Scope123456")["access_token"])
    page = client.get("/api/v1/system/users?page=1&page_size=100", headers=scoped).json()["data"]["items"]
    visible = {item["username"] for item in page}
    assert {"scope_a_manager", "scope_a_staff"} <= visible
    assert not ({"scope_a_child", "scope_b_staff", "admin"} & visible)

    tree = client.get("/api/v1/system/depts", headers=scoped).json()["data"]
    nodes = _dept_ids(tree)
    assert dept_a in nodes
    assert not ({dept_a_child, dept_b} & nodes)

    online = client.get("/api/v1/monitor/online", headers=scoped).json()["data"]
    assert online and {item["dept_id"] for item in online} == {dept_a}
    assert "scope_b_staff" not in {item["username"] for item in online}

    logs_response = client.get("/api/v1/monitor/login-logs?page=1&page_size=100", headers=scoped)
    assert logs_response.status_code == 200, logs_response.text
    logs = logs_response.json()["data"]["items"]
    assert "scope_a_manager" in {item["username"] for item in logs}
    assert "scope_b_staff" not in {item["username"] for item in logs}

    extra = created(
        client.post("/api/v1/system/depts", headers=headers, json={"name": "范围B子部", "parent_id": dept_b, "sort": 1})
    )["id"]
    operations = client.get("/api/v1/monitor/operation-logs?page=1&page_size=100", headers=scoped).json()["data"][
        "items"
    ]
    assert "admin" not in {item["username"] for item in operations}

    denied = client.put(f"/api/v1/system/depts/{dept_b}", headers=scoped, json={"name": "改坏"})
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "SYSTEM_DEPT_FORBIDDEN"
    outside = client.post("/api/v1/system/depts", headers=scoped, json={"name": "挂靠B部", "parent_id": dept_b})
    assert outside.status_code == 403, outside.text
    inside = client.post("/api/v1/system/depts", headers=scoped, json={"name": "A部平台组", "parent_id": dept_a})
    assert inside.status_code == 200, inside.text
    dept_a_sub = inside.json()["data"]["id"]

    b_session = login(client, "scope_b_staff", "Scope123456")
    b_token = auth(b_session["access_token"])
    b_sid = next(
        item["id"] for item in client.get("/api/v1/auth/sessions", headers=b_token).json()["data"] if item["current"]
    )
    kick = client.delete(f"/api/v1/monitor/online/{b_sid}", headers=scoped)
    assert kick.status_code == 403, kick.text
    assert kick.json()["code"] == "MONITOR_KICK_FORBIDDEN"
    assert client.get("/api/v1/auth/me", headers=b_token).status_code == 200

    a_sid = next(
        item["id"] for item in client.get("/api/v1/auth/sessions", headers=scoped).json()["data"] if item["current"]
    )
    assert client.delete(f"/api/v1/monitor/online/{a_sid}", headers=scoped).status_code == 200

    no_perm = client.get("/api/v1/monitor/login-logs/export", headers=b_token, params={"keyword": "scope_a"})
    assert no_perm.status_code == 403, no_perm.text

    scoped = auth(login(client, "scope_a_manager", "Scope123456")["access_token"])
    export = client.get("/api/v1/monitor/login-logs/export", headers=scoped, params={"keyword": "scope_b_staff"})
    assert export.status_code == 200, export.text
    assert "scope_b_staff" not in export.text
    in_scope = client.get("/api/v1/monitor/login-logs/export", headers=scoped, params={"keyword": "scope_a"})
    assert in_scope.status_code == 200, in_scope.text
    assert "scope_a_manager" in in_scope.text

    for scope_name, dept_ids, expect_visible, expect_hidden in (
        ("dept_and_below", [], {"scope_a_child"}, {"scope_b_staff"}),
        ("custom", [dept_b], {"scope_b_staff"}, {"scope_a_staff"}),
        ("self", [], {"scope_a_manager"}, {"scope_a_staff", "scope_b_staff"}),
    ):
        changed = client.put(
            f"/api/v1/system/roles/{role_id}/data-scope",
            headers=headers,
            json={"data_scope": scope_name, "dept_ids": dept_ids},
        )
        assert changed.status_code == 200, changed.text
        items = client.get("/api/v1/system/users?page=1&page_size=100", headers=scoped).json()["data"]["items"]
        names = {item["username"] for item in items}
        assert expect_visible <= names, (scope_name, names)
        assert not (expect_hidden & names), (scope_name, names)

    for user_id in users.values():
        assert client.delete(f"/api/v1/system/users/{user_id}", headers=headers).status_code == 200
    for dept_id in (dept_a_sub, extra, dept_a_child, dept_b, dept_a):
        assert client.delete(f"/api/v1/system/depts/{dept_id}", headers=headers).status_code == 200, dept_id
    assert client.delete(f"/api/v1/system/roles/{role_id}", headers=headers).status_code == 200
