from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth, login

MIN_LENGTH_KEY = "security.password_min_length"


def _config_id(client: TestClient, headers: dict[str, str], key: str) -> int:
    items = client.get("/api/v1/system/configs?group=security", headers=headers).json()["data"]
    return next(int(item["id"]) for item in items if item["key"] == key)


def _create_user(client: TestClient, headers: dict[str, str], username: str, password: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/system/users",
        headers=headers,
        json={"username": username, "nickname": "策略测试", "password": password, "must_change_password": False},
    )


def test_password_policy_blocks_common_and_username_derived(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    common = _create_user(client, headers, "policy_weak", "Password1")
    assert common.status_code == 422, common.text
    assert "常见" in common.json()["message"]

    derived = _create_user(client, headers, "nameduser", "Nameduser12345")
    assert derived.status_code == 422, derived.text
    assert "用户名" in derived.json()["message"]


def test_password_policy_follows_system_config(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    config_id = _config_id(client, headers, MIN_LENGTH_KEY)
    assert client.put(f"/api/v1/system/configs/{config_id}", headers=headers, json={"value": "12"}).status_code == 200
    created_id: int | None = None
    try:
        public = client.get("/api/v1/system/public-config").json()["data"]
        assert public["auth.password_min_length"] == 12
        assert "12" in public["auth.password_rule"]

        short = _create_user(client, headers, "policy_short", "Pass12345")
        assert short.status_code == 422, short.text
        assert "12" in short.json()["message"]

        strong = _create_user(client, headers, "policy_strong", "Zq9Km2Tr7Xu4")
        assert strong.status_code == 200, strong.text
        created_id = strong.json()["data"]["id"]

        reset = client.post(
            f"/api/v1/system/users/{created_id}/reset-password",
            headers=headers,
            json={"new_password": "Ab12345678", "must_change_password": False},
        )
        assert reset.status_code == 422, reset.text
        assert "12" in reset.json()["message"]

        change = client.post(
            "/api/v1/auth/change-password",
            headers=auth(login(client, "policy_strong", "Zq9Km2Tr7Xu4")["access_token"]),
            json={"old_password": "Zq9Km2Tr7Xu4", "new_password": "Xy7Bq2Lm4Kn9"},
        )
        assert change.status_code == 200, change.text
    finally:
        client.put(f"/api/v1/system/configs/{config_id}", headers=headers, json={"value": "8"})
        if created_id is not None:
            client.delete(f"/api/v1/system/users/{created_id}", headers=headers)


def test_login_does_not_reveal_account_existence_or_lock_state(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = _create_user(client, headers, "lock_target", "LockTarget123")
    assert created.status_code == 200, created.text
    user_id = created.json()["data"]["id"]

    unknown = client.post("/api/v1/auth/login", json={"username": "ghost_user", "password": "Whatever12345"})
    wrong = client.post("/api/v1/auth/login", json={"username": "lock_target", "password": "Whatever12345"})
    # 用户名是否存在都必须返回同一个错误码和文案，否则可以据此枚举账号
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["code"] == wrong.json()["code"] == "AUTH_INVALID_CREDENTIALS"

    for _ in range(5):
        assert client.post(
            "/api/v1/auth/login", json={"username": "lock_target", "password": "BadPass12345"}
        ).status_code == 401

    locked = client.post("/api/v1/auth/login", json={"username": "lock_target", "password": "LockTarget123"})
    # 口令正确时才提示锁定，锁定状态本身不再成为枚举线索
    assert locked.status_code == 423, locked.text
    assert locked.json()["code"] == "AUTH_ACCOUNT_LOCKED"

    assert client.post("/api/v1/auth/login", json={"username": "ghost_user", "password": "LockTarget123"}).status_code == 401

    admin = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Owner123456"})
    assert admin.status_code == 200, admin.text
    assert client.delete(f"/api/v1/system/users/{user_id}", headers=headers).status_code == 200


def test_reset_password_clears_account_lock(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = _create_user(client, headers, "lock_reset", "LockReset12345")
    assert created.status_code == 200, created.text
    user_id = created.json()["data"]["id"]
    try:
        for _ in range(5):
            assert client.post(
                "/api/v1/auth/login", json={"username": "lock_reset", "password": "BadPass12345"}
            ).status_code == 401
        blocked = client.post("/api/v1/auth/login", json={"username": "lock_reset", "password": "LockReset12345"})
        assert blocked.status_code == 423, blocked.text

        listed = client.get(f"/api/v1/system/users/{user_id}", headers=headers).json()["data"]
        assert listed["locked_until"], "锁定截止时间必须回传，否则管理员无法判断账号状态"

        reset = client.post(
            f"/api/v1/system/users/{user_id}/reset-password",
            headers=headers,
            json={"new_password": "Unlocked99999", "must_change_password": False},
        )
        assert reset.status_code == 200, reset.text

        detail = client.get(f"/api/v1/system/users/{user_id}", headers=headers).json()["data"]
        assert detail["locked_until"] is None
        assert client.post(
            "/api/v1/auth/login", json={"username": "lock_reset", "password": "Unlocked99999"}
        ).status_code == 200
    finally:
        assert client.delete(f"/api/v1/system/users/{user_id}", headers=headers).status_code == 200


def test_password_min_length_config_cannot_go_below_enforced_floor(
    client: TestClient, admin_token: str
) -> None:
    headers = auth(admin_token)
    config_id = _config_id(client, headers, MIN_LENGTH_KEY)
    assert client.put(f"/api/v1/system/configs/{config_id}", headers=headers, json={"value": "3"}).status_code == 200
    weak_id: int | None = None
    try:
        # 配置写 3 时服务端仍按 8 位校验，对外规则必须报同一个数字，否则前端放行、后端报错
        public = client.get("/api/v1/system/public-config").json()["data"]
        assert public["auth.password_min_length"] == 8
        assert "8" in public["auth.password_rule"]

        short = _create_user(client, headers, "floor_short", "Ab1234")
        assert short.status_code == 422, short.text
        floor_ok = _create_user(client, headers, "floor_pass", "Ab123456")
        assert floor_ok.status_code == 200, floor_ok.text
        weak_id = floor_ok.json()["data"]["id"]
    finally:
        client.put(f"/api/v1/system/configs/{config_id}", headers=headers, json={"value": "8"})
        if weak_id is not None:
            client.delete(f"/api/v1/system/users/{weak_id}", headers=headers)


def test_sensitive_endpoints_are_rate_limited(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    codes = [client.get("/api/v1/monitor/login-logs/export", headers=headers).status_code for _ in range(12)]
    assert 200 in codes
    # 导出会拉取大量审计数据，超过阈值后必须限流而不是继续压库
    assert codes[-1] == 429, codes
    body = client.get("/api/v1/monitor/login-logs/export", headers=headers)
    assert body.json()["code"] == "AUTH_RATE_LIMITED"


def test_user_update_can_assign_roles_atomically(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    roles = {item["code"]: item["id"] for item in client.get("/api/v1/system/roles", headers=headers).json()["data"]}
    created = _create_user(client, headers, "atomic_user", "AtomicUser123")
    assert created.status_code == 200, created.text
    user_id = created.json()["data"]["id"]

    updated = client.put(
        f"/api/v1/system/users/{user_id}",
        headers=headers,
        json={"nickname": "一次提交", "role_ids": [roles["auditor"]]},
    )
    assert updated.status_code == 200, updated.text
    assert [role["id"] for role in updated.json()["data"]["roles"]] == [roles["auditor"]]

    cleared = client.put(f"/api/v1/system/users/{user_id}", headers=headers, json={"role_ids": []})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["roles"] == []

    # 未携带 role_ids 的旧调用不能顺带改动角色
    client.put(f"/api/v1/system/users/{user_id}", headers=headers, json={"role_ids": [roles["auditor"]]})
    untouched = client.put(f"/api/v1/system/users/{user_id}", headers=headers, json={"nickname": "只改昵称"})
    assert [role["id"] for role in untouched.json()["data"]["roles"]] == [roles["auditor"]]

    limited_role = client.post(
        "/api/v1/system/roles",
        headers=headers,
        json={"name": "资料改员", "code": "profile_editor_only", "data_scope": "all"},
    )
    assert limited_role.status_code == 200, limited_role.text
    limited_id = limited_role.json()["data"]["id"]
    assert client.put(
        f"/api/v1/system/roles/{limited_id}/permissions",
        headers=headers,
        json={"permission_codes": ["system:admin:access", "system:user:list", "system:user:update"]},
    ).status_code == 200
    helper = _create_user(client, headers, "profile_editor", "ProfileEditor123")
    assert helper.status_code == 200, helper.text
    client.put(
        f"/api/v1/system/users/{helper.json()['data']['id']}",
        headers=headers,
        json={"role_ids": [limited_id]},
    )
    editor = auth(login(client, "profile_editor", "ProfileEditor123")["access_token"])
    forbidden = client.put(
        f"/api/v1/system/users/{user_id}",
        headers=editor,
        json={"nickname": "越权改角色", "role_ids": []},
    )
    assert forbidden.status_code == 403, forbidden.text
    assert "分配角色" in forbidden.json()["message"]

    for extra in (user_id, helper.json()["data"]["id"]):
        assert client.delete(f"/api/v1/system/users/{extra}", headers=headers).status_code == 200
    for role_id in (limited_id,):
        assert client.delete(f"/api/v1/system/roles/{role_id}", headers=headers).status_code == 200


def test_role_options_and_last_login_ip_are_minimal(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    options = client.get("/api/v1/system/roles/options", headers=headers).json()["data"]
    assert options
    assert all(set(item) == {"id", "name", "code"} for item in options)

    role = client.post(
        "/api/v1/system/roles",
        headers=headers,
        json={"name": "用户查看员", "code": "user_reader_only", "data_scope": "all"},
    )
    assert role.status_code == 200, role.text
    role_id = role.json()["data"]["id"]
    assert client.put(
        f"/api/v1/system/roles/{role_id}/permissions",
        headers=headers,
        json={"permission_codes": ["system:admin:access", "system:user:list", "system:dept:list"]},
    ).status_code == 200

    created = client.post(
        "/api/v1/system/users",
        headers=headers,
        json={
            "username": "ip_guard",
            "nickname": "IP 隔离",
            "password": "IpGuard12345",
            "must_change_password": False,
            "role_ids": [role_id],
        },
    )
    assert created.status_code == 200, created.text
    viewer = auth(login(client, "ip_guard", "IpGuard12345")["access_token"])
    page = client.get("/api/v1/system/users?page=1&page_size=100", headers=viewer).json()["data"]["items"]
    rows = {item["username"] for item in page}
    assert "ip_guard" in rows
    # 没有登录日志权限的角色看不到任何人的最后登录 IP，包括自己的
    assert all(item["last_login_ip"] is None for item in page)

    admin_page = client.get("/api/v1/system/users?page=1&page_size=100", headers=headers).json()["data"]["items"]
    admin_rows = {item["username"]: item for item in admin_page}
    assert admin_rows["admin"]["last_login_ip"]

    assert client.delete(f"/api/v1/system/users/{created.json()['data']['id']}", headers=headers).status_code == 200
    assert client.delete(f"/api/v1/system/roles/{role_id}", headers=headers).status_code == 200
