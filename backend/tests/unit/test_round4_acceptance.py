"""第 4 轮发布验收的修复回归测试。

第 4 轮沿着三条此前没深挖的链路走：权限与数据范围、内容/上传/导出、前端会话。
发现的问题都落在「链路两端不对称」这一类上：

- 上传落盘后才做类型校验，校验失败的文件没人删（磁盘只增不减）；
- CSV 导入先 ``read()`` 全量再判大小，8 MB 上限形同虚设；
- 封禁用户列表没走数据范围，部门管理员能跨部门看到用户名、部门与登录 IP；
- 批量用户按钮用 ``user:status`` / ``user:delete`` 授权，后端却要 ``user:batch``；
- API Key / IP 规则 / 封禁页的行内按钮完全没做权限判断；
- 多标签页并发刷新会互相把会话顶掉（重放检测误伤并发）；
- 刷新接口的 5xx/网络故障被当成「登录过期」，直接把人踢到登录页；
- 公开接口带过期 token 时返回 401，前台公告页变成必须登录才能看。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import auth

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "app" / "web" / "static" / "js"


def _data(response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _created(response) -> dict[str, Any]:
    return _data(response)


# ------------------------- 上传 / 导入的落盘边界 -------------------------


def test_rejected_upload_leaves_no_orphan_file(
    client: TestClient, admin_token: str, tmp_path: Path, monkeypatch
) -> None:
    """类型校验失败的上传必须在磁盘上不留任何文件。

    校验发生在落盘之后，若不显式清理，这份文件在库里没有任何行，
    ``cleanup_orphan_files`` 只扫 DB 记录，永远回收不到。
    """
    import app.kernel.files as files_mod

    root = tmp_path / "store"
    monkeypatch.setattr(files_mod, "STORAGE_ROOT", root)

    response = client.post(
        "/api/v1/auth/avatar",
        headers=auth(admin_token),
        files={"file": ("note.txt", b"definitely not an image", "text/plain")},
    )
    assert 400 <= response.status_code < 500, response.text
    leftovers = [path for path in root.rglob("*") if path.is_file()]
    assert leftovers == [], f"校验失败后仍残留文件：{leftovers}"


def test_import_users_reads_with_size_cap(
    client: TestClient, admin_token: str, monkeypatch
) -> None:
    """导入必须在读取阶段就封顶，而不是先全量读进内存再判断。"""
    import app.modules.sys.system.router as sys_router

    monkeypatch.setattr(sys_router, "MAX_IMPORT_BYTES", 16)
    monkeypatch.setattr(sys_router, "MAX_IMPORT_MB", 0)

    payload = b"username,password\n" + b"x" * 128
    response = client.post(
        "/api/v1/system/users/import",
        headers=auth(admin_token),
        files={"file": ("users.csv", payload, "text/csv")},
    )
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "IMPORT_TOO_LARGE"


# ---------------------------- 权限 / 数据范围 ----------------------------


def test_export_requires_admin_access(client: TestClient, admin_token: str) -> None:
    """只有 ``*:export`` 而没有后台访问权的角色不得导出。

    导出属于后台能力，缺 ``require_admin`` 会出现「进不了后台却能拉走整份
    用户 CSV」的越权边界。
    """
    headers = auth(admin_token)
    role = _created(
        client.post(
            "/api/v1/system/roles",
            headers=headers,
            json={"name": "导出员", "code": "export_only_role", "data_scope": "all"},
        )
    )
    _data(
        client.put(
            f"/api/v1/system/roles/{role['id']}/permissions",
            headers=headers,
            json={"permission_codes": ["system:user:export"]},
        )
    )
    _created(
        client.post(
            "/api/v1/system/users",
            headers=headers,
            json={
                "username": "export_only_staff",
                "password": "Export123456",
                "nickname": "导出员",
                "must_change_password": False,
                "role_ids": [role["id"]],
            },
        )
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": "export_only_staff", "password": "Export123456"},
    )
    assert token.status_code == 200, token.text
    staff = auth(token.json()["data"]["access_token"])

    response = client.get("/api/v1/export/users", headers=staff)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "AUTH_FORBIDDEN"


def test_batch_bar_gates_on_batch_permission() -> None:
    """批量操作按钮必须与后端同一个权限码，否则按钮可见但必 403。"""
    template = (ROOT / "app" / "web" / "templates" / "pages" / "system" / "user.html").read_text(
        encoding="utf-8"
    )
    start = template.index("batch_bar")
    block = template[start : template.index("]) }}", start)]
    assert "system:user:batch" in block
    assert "system:user:status" not in block
    assert "system:user:delete" not in block


def test_row_actions_are_permission_gated() -> None:
    """行内按钮缺 ``can()`` 时，只读角色会看到一片必 403 的按钮。"""
    expected = {
        "pages/security/api-key.js": ["security:api-key:update", "security:api-key:delete"],
        "pages/security/ip-rule.js": [
            "security:ip-rule:update",
            "security:ip-rule:create",
            "security:ip-rule:delete",
        ],
        "pages/security/ban.js": ["security:user-ban:ban"],
        "pages/system/theme.js": ["system:theme:update"],
    }
    for relative, codes in expected.items():
        source = (JS / relative).read_text(encoding="utf-8")
        for code in codes:
            assert f"can('{code}')" in source, f"{relative} 缺少 {code} 的权限判断"


# ------------------------------- 公开接口 -------------------------------


def test_public_notices_survive_invalid_token(client: TestClient) -> None:
    """公开公告接口带坏 token 时降级为匿名，而不是 401。"""
    response = client.get(
        "/api/v1/content/notices/published",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "OK"


def test_notice_status_filter_accepts_offline(client: TestClient, admin_token: str) -> None:
    """``unpublish`` 写入的 ``offline`` 必须能作为筛选条件，否则该状态不可查。"""
    response = client.get(
        "/api/v1/content/notices",
        headers=auth(admin_token),
        params={"status": "offline"},
    )
    assert response.status_code == 200, response.text


# ------------------------------ 会话与刷新 ------------------------------


def _refresh_cookie(response) -> str | None:
    """从响应的 Set-Cookie 里取刷新凭证；没下发返回 None。"""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith("fastcms_refresh="):
            return header.split(";", 1)[0].split("=", 1)[1]
    return None


def test_refresh_grace_does_not_revoke_session(client: TestClient) -> None:
    """多标签页并发刷新：第二代 token 被重放时补发 access token，不撤销会话。

    用显式 Cookie 头而不是 cookie jar：这里要精确控制「拿哪一代 token 去换」，
    而 jar 会把同名不同作用域的 cookie 混在一起。
    """
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Owner123456"})
    assert login.status_code == 200, login.text
    old_cookie = _refresh_cookie(login)
    assert old_cookie

    first = client.post("/api/v1/auth/refresh", headers={"Cookie": f"fastcms_refresh={old_cookie}"})
    assert first.status_code == 200, first.text
    rotated_cookie = _refresh_cookie(first)
    assert rotated_cookie and rotated_cookie != old_cookie

    second = client.post("/api/v1/auth/refresh", headers={"Cookie": f"fastcms_refresh={old_cookie}"})
    assert second.status_code == 200, second.text
    assert second.json()["data"]["access_token"]
    # 宽限分支不轮换：不能再下发一份新的 refresh cookie
    assert _refresh_cookie(second) is None

    # 会话没被撤销，最新凭证还能继续用
    still_valid = client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"fastcms_refresh={rotated_cookie}"}
    )
    assert still_valid.status_code == 200, still_valid.text


def test_frontend_distinguishes_transient_refresh_failure() -> None:
    """刷新失败必须区分「凭证失效」与「临时故障」，后者不能清登录态。"""
    source = (JS / "core" / "request.js").read_text(encoding="utf-8")
    assert "REQUEST_TIMEOUT_MS" in source
    assert "timeoutSignal" in source
    # 401/403 才返回 null（确定性失效），其余状态码抛错
    assert "response.status === 401 || response.status === 403" in source
    assert "throw new ApiError" in source


def test_frontend_boot_has_single_refresh_and_error_path() -> None:
    """启动链路：只刷新一次、失败有可见错误、未登录能回到原页面。"""
    admin = (JS / "shell" / "admin.js").read_text(encoding="utf-8")
    assert "retry: false" in admin
    assert "next: true" in admin

    app_source = (JS / "app.js").read_text(encoding="utf-8")
    assert "main().catch(" in app_source

    auth_source = (JS / "shell" / "auth.js").read_text(encoding="utf-8")
    assert "function safeNext(" in auth_source
    # 开放重定向必须被挡掉
    assert "startsWith('//')" in auth_source
