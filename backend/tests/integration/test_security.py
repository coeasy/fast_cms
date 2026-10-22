from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import auth


def _unwrap(response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _headers(token: str) -> dict[str, str]:
    return auth(token)


def _create_rule(client: TestClient, token: str, **payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"target": "203.0.113.7", "scope": "all"}
    body.update(payload)
    return _unwrap(client.post("/api/v1/security/ip-rules", headers=_headers(token), json=body))


# ------------------------------- IP 规则 -------------------------------


def test_ip_rule_rejects_invalid_target(client: TestClient, admin_token: str) -> None:
    for target in ("", "abc", "203.0.113.0/33", "999.1.1.1", "10.0.0.1/abc"):
        response = client.post(
            "/api/v1/security/ip-rules",
            headers=_headers(admin_token),
            json={"target": target},
        )
        assert response.status_code == 422, f"{target!r} 应该被拒绝"
        assert response.json()["code"] == "VALIDATION_ERROR"


def test_ip_rule_normalizes_cidr_and_rejects_duplicates(
    client: TestClient, admin_token: str
) -> None:
    created = _create_rule(client, admin_token, target="203.0.113.5/24", reason="测试")
    assert created["canonical_target"] == "203.0.113.0/24"
    assert created["target"] == "203.0.113.0/24"
    assert created["kind"] == "block"
    assert created["scope"] == "all"

    duplicated = client.post(
        "/api/v1/security/ip-rules",
        headers=_headers(admin_token),
        json={"target": "203.0.113.9/24"},
    )
    assert duplicated.status_code == 409
    assert duplicated.json()["code"] == "SECURITY_IP_RULE_DUPLICATED"


def test_ip_rule_supports_ipv6_and_allow_wins(client: TestClient, admin_token: str) -> None:
    block = _create_rule(client, admin_token, target="2001:db8::/32", reason="封禁整段")
    allow = _create_rule(
        client, admin_token, target="2001:db8::1", kind="allow", scope="all", reason="白名单"
    )
    assert allow["kind"] == "allow"

    # allow 优先：单地址命中 allow 后不再被所在网段的 block 拦住
    lookup = _unwrap(
        client.get("/api/v1/security/ip-rules/lookup/2001:db8::1", headers=_headers(admin_token))
    )
    assert lookup["blocked"] is False
    other = _unwrap(
        client.get("/api/v1/security/ip-rules/lookup/2001:db8::2", headers=_headers(admin_token))
    )
    assert other["blocked"] is True
    assert other["scopes"]["api"]["rule"]["id"] == block["id"]


def test_ip_rule_scope_limits_where_it_applies(client: TestClient, admin_token: str) -> None:
    """规则按作用域生效：只封接口的规则不该把后台页面也关掉。"""
    _create_rule(client, admin_token, target="198.51.100.0/24", scope="api")
    lookup = _unwrap(
        client.get("/api/v1/security/ip-rules/lookup/198.51.100.5", headers=_headers(admin_token))
    )
    assert lookup["blocked"] is True
    assert lookup["scopes"]["api"]["blocked"] is True
    assert lookup["scopes"]["admin"]["blocked"] is False
    assert lookup["scopes"]["portal"]["blocked"] is False

    # 同一网段在别的作用域可以并存，不会因为唯一约束被拒
    _create_rule(client, admin_token, target="198.51.100.0/24", scope="admin")
    again = _unwrap(
        client.get("/api/v1/security/ip-rules/lookup/198.51.100.5", headers=_headers(admin_token))
    )
    assert again["scopes"]["admin"]["blocked"] is True

    listing = _unwrap(
        client.get(
            "/api/v1/security/ip-rules",
            headers=_headers(admin_token),
            params={"scope": "admin", "keyword": "198.51.100"},
        )
    )
    assert any(item["target"] == "198.51.100.0/24" for item in listing["items"])


def test_disabled_ip_rule_stops_blocking(client: TestClient, admin_token: str) -> None:
    rule = _create_rule(client, admin_token, target="192.0.2.10")
    toggled = _unwrap(
        client.post(
            f"/api/v1/security/ip-rules/{rule['id']}/status",
            headers=_headers(admin_token),
            params={"status": "disabled"},
        )
    )
    assert toggled["status"] == "disabled"
    lookup = _unwrap(
        client.get("/api/v1/security/ip-rules/lookup/192.0.2.10", headers=_headers(admin_token))
    )
    assert lookup["blocked"] is False


def test_ip_rule_lookup_rejects_garbage(client: TestClient, admin_token: str) -> None:
    response = client.get(
        "/api/v1/security/ip-rules/lookup/not-an-ip", headers=_headers(admin_token)
    )
    assert response.status_code == 422
    assert response.json()["code"] == "SECURITY_IP_INVALID"


# ------------------------------- 用户封禁 -------------------------------


def _create_user(client: TestClient, admin_token: str, username: str) -> dict[str, Any]:
    return _unwrap(
        client.post(
            "/api/v1/system/users",
            headers=_headers(admin_token),
            json={
                "username": username,
                "nickname": username,
                "password": "Target12345",
                "status": "active",
            },
        )
    )


def test_ban_blocks_login_and_kills_existing_token(
    client: TestClient, admin_token: str
) -> None:
    victim = _create_user(client, admin_token, "ban_target")
    victim_token = client.post(
        "/api/v1/auth/login", json={"username": "ban_target", "password": "Target12345"}
    ).json()["data"]["access_token"]

    # 封禁前可以正常访问
    assert client.get(
        "/api/v1/auth/me", headers=_headers(victim_token)
    ).status_code == 200

    banned = _unwrap(
        client.post(
            f"/api/v1/security/users/{victim['id']}/ban",
            headers=_headers(admin_token),
            json={"reason": "测试封禁", "ban_ip": False},
        )
    )
    assert banned["banned"] is True
    assert banned["ban_reason"] == "测试封禁"

    # 已签发的 token 必须立刻失效
    me = client.get("/api/v1/auth/me", headers=_headers(victim_token))
    assert me.status_code == 403
    assert me.json()["code"] == "AUTH_ACCOUNT_BANNED"
    assert me.json()["details"]["reason"] == "测试封禁"

    # 登录同样被拒
    login = client.post(
        "/api/v1/auth/login", json={"username": "ban_target", "password": "Target12345"}
    )
    assert login.status_code == 403
    assert login.json()["code"] == "AUTH_ACCOUNT_BANNED"


def test_unban_restores_login(client: TestClient, admin_token: str) -> None:
    victim = _create_user(client, admin_token, "unban_target")
    client.post(
        f"/api/v1/security/users/{victim['id']}/ban",
        headers=_headers(admin_token),
        json={"reason": "临时", "ban_ip": False},
    )
    blocked = client.post(
        "/api/v1/auth/login", json={"username": "unban_target", "password": "Target12345"}
    )
    assert blocked.status_code == 403

    _unwrap(
        client.post(
            f"/api/v1/security/users/{victim['id']}/unban", headers=_headers(admin_token)
        )
    )
    restored = client.post(
        "/api/v1/auth/login", json={"username": "unban_target", "password": "Target12345"}
    )
    assert restored.status_code == 200, restored.text


def test_cannot_ban_self(client: TestClient, admin_token: str) -> None:
    me = _unwrap(client.get("/api/v1/auth/me", headers=_headers(admin_token)))
    response = client.post(
        f"/api/v1/security/users/{me['user']['id']}/ban",
        headers=_headers(admin_token),
        json={"reason": "自作自受", "ban_ip": False},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SECURITY_SELF_BAN"


def test_banned_user_listing(client: TestClient, admin_token: str) -> None:
    victim = _create_user(client, admin_token, "listed_ban")
    client.post(
        f"/api/v1/security/users/{victim['id']}/ban",
        headers=_headers(admin_token),
        json={"reason": "列表测试", "ban_ip": False},
    )
    listing = _unwrap(
        client.get(
            "/api/v1/security/banned-users",
            headers=_headers(admin_token),
            params={"keyword": "listed_ban"},
        )
    )
    assert listing["total"] == 1
    assert listing["items"][0]["username"] == "listed_ban"
    assert listing["items"][0]["banned"] is True


def test_ban_out_of_scope_user_is_forbidden(client: TestClient, admin_token: str) -> None:
    """数据范围必须在封禁接口上同样生效，否则可以越权处置任意账号。"""
    headers = _headers(admin_token)

    def created(response) -> dict[str, Any]:
        assert response.status_code == 200, response.text
        return response.json()["data"]

    dept_a = created(client.post("/api/v1/system/depts", headers=headers, json={"name": "封禁A部", "sort": 71}))["id"]
    dept_b = created(client.post("/api/v1/system/depts", headers=headers, json={"name": "封禁B部", "sort": 72}))["id"]
    role = created(
        client.post(
            "/api/v1/system/roles",
            headers=headers,
            json={"name": "封禁A管理员", "code": "ban_scope_a_admin", "data_scope": "dept"},
        )
    )
    created(
        client.put(
            f"/api/v1/system/roles/{role['id']}/permissions",
            headers=headers,
            json={
                "permission_codes": [
                    "system:admin:access",
                    "system:user:list",
                    "security:user-ban:list",
                    "security:user-ban:ban",
                ]
            },
        )
    )
    outsider = created(
        client.post(
            "/api/v1/system/users",
            headers=headers,
            json={
                "username": "ban_scope_b_staff",
                "password": "Scope123456",
                "nickname": "B部员工",
                "must_change_password": False,
                "dept_id": dept_b,
            },
        )
    )["id"]

    scoped_token = client.post(
        "/api/v1/auth/login", json={"username": "ban_scope_a_manager", "password": "Scope123456"}
    )
    # 上面没建 A 部管理员账号，这里补建并登录
    manager = created(
        client.post(
            "/api/v1/system/users",
            headers=headers,
            json={
                "username": "ban_scope_a_manager",
                "password": "Scope123456",
                "nickname": "A部管理员",
                "must_change_password": False,
                "dept_id": dept_a,
                "role_ids": [role["id"]],
            },
        )
    )["id"]
    del manager
    if scoped_token.status_code != 200:
        scoped_token = client.post(
            "/api/v1/auth/login", json={"username": "ban_scope_a_manager", "password": "Scope123456"}
        )
    assert scoped_token.status_code == 200, scoped_token.text
    scoped = _headers(scoped_token.json()["data"]["access_token"])

    forbidden = client.post(
        f"/api/v1/security/users/{outsider}/ban",
        headers=scoped,
        json={"reason": "越权尝试", "ban_ip": False},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "SYSTEM_USER_FORBIDDEN"


# ------------------------------- API Key -------------------------------


def test_api_key_is_returned_once_and_never_again(client: TestClient, admin_token: str) -> None:
    created = _unwrap(
        client.post(
            "/api/v1/security/api-keys",
            headers=_headers(admin_token),
            json={"name": "测试对接", "scopes": "system:user:list", "remark": "一次性"},
        )
    )
    assert created["key"].startswith("fck_")
    assert "key" not in created or created["key"]

    listing = _unwrap(
        client.get("/api/v1/security/api-keys", headers=_headers(admin_token), params={"keyword": "测试对接"})
    )
    assert listing["items"][0]["key_prefix"] == created["key"][:12]
    # 列表里绝不再出现明文或完整哈希
    assert "key" not in listing["items"][0]
    assert "key_hash" not in listing["items"][0]

    disabled = _unwrap(
        client.post(
            f"/api/v1/security/api-keys/{created['id']}/status",
            headers=_headers(admin_token),
            params={"status": "disabled"},
        )
    )
    assert disabled["status"] == "disabled"

    _unwrap(client.delete(f"/api/v1/security/api-keys/{created['id']}", headers=_headers(admin_token)))


def test_api_key_rejects_duplicate_name(client: TestClient, admin_token: str) -> None:
    _unwrap(
        client.post(
            "/api/v1/security/api-keys",
            headers=_headers(admin_token),
            json={"name": "重名测试"},
        )
    )
    duplicated = client.post(
        "/api/v1/security/api-keys",
        headers=_headers(admin_token),
        json={"name": "重名测试"},
    )
    assert duplicated.status_code == 409
    assert duplicated.json()["code"] == "SECURITY_API_KEY_DUPLICATED"


def test_security_endpoints_require_permission(client: TestClient, admin_token: str) -> None:
    response = client.get("/api/v1/security/ip-rules")
    assert response.status_code == 401
    bad = client.get("/api/v1/security/ip-rules", headers={"Authorization": "Bearer nonsense"})
    assert bad.status_code == 401


# --------------------- API Key 认证链路 ---------------------


def _create_key(client: TestClient, token: str, name: str, scopes: str) -> dict[str, Any]:
    return _unwrap(
        client.post(
            "/api/v1/security/api-keys",
            headers=_headers(token),
            json={"name": name, "scopes": scopes},
        )
    )


def test_api_key_authenticates_and_carries_its_scopes(
    client: TestClient, admin_token: str
) -> None:
    """Key 必须真的能当凭证用，而且权限就等于它声明的 scopes。

    服务账号不挂角色，所以权限不可能来自角色；声明什么就有什么。
    """
    created = _create_key(client, admin_token, "链路自测", "system:user:list,system:dept:list")

    me = client.get("/api/v1/auth/me", headers=_headers(created["key"]))
    assert me.status_code == 200, me.text
    payload = me.json()["data"]
    assert payload["via_api_key"] is True
    assert payload["session"] is None
    assert payload["user"]["username"].startswith("svc_")
    assert set(payload["permissions"]) == {"system:user:list", "system:dept:list"}


def test_api_key_grants_access_to_declared_endpoints(
    client: TestClient, admin_token: str
) -> None:
    # system/users 额外要求 system:admin:access，所以声明里要带上
    created = _create_key(
        client, admin_token, "端点自测", "system:admin:access,system:user:list"
    )
    headers = _headers(created["key"])
    assert client.get("/api/v1/system/users", headers=headers, params={"page": 1}).status_code == 200
    # 没声明的接口必须挡住
    denied = client.get("/api/v1/security/ip-rules", headers=headers, params={"page": 1})
    assert denied.status_code == 403
    assert denied.json()["code"] == "AUTH_FORBIDDEN"


def test_api_key_cannot_use_session_only_endpoints(
    client: TestClient, admin_token: str
) -> None:
    """退出登录、心跳、改密都建立在会话上，Key 没有会话，必须明确拒绝。"""
    created = _create_key(client, admin_token, "会话边界", "system:admin:access")
    headers = _headers(created["key"])
    for path in ("/api/v1/auth/logout", "/api/v1/auth/heartbeat"):
        response = client.post(path, headers=headers)
        assert response.status_code == 400, f"{path} 应拒绝"
        assert response.json()["code"] == "AUTH_SESSION_REQUIRED"
    listed = client.get("/api/v1/auth/sessions", headers=headers)
    assert listed.status_code == 400
    assert listed.json()["code"] == "AUTH_SESSION_REQUIRED"


def test_api_key_stops_working_once_disabled(client: TestClient, admin_token: str) -> None:
    created = _create_key(client, admin_token, "停用自测", "system:user:list")
    headers = _headers(created["key"])
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    _unwrap(
        client.post(
            f"/api/v1/security/api-keys/{created['id']}/status",
            headers=_headers(admin_token),
            params={"status": "disabled"},
        )
    )
    after = client.get("/api/v1/auth/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["code"] == "AUTH_TOKEN_INVALID"


def test_api_key_rejects_unknown_scope(client: TestClient, admin_token: str) -> None:
    response = client.post(
        "/api/v1/security/api-keys",
        headers=_headers(admin_token),
        json={"name": "瞎编权限", "scopes": "no:such:code"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "SECURITY_API_KEY_SCOPE_UNKNOWN"


def test_service_account_cannot_login_with_password(
    client: TestClient, admin_token: str
) -> None:
    """服务账号的口令是随机值，且登录路径显式拒绝，双保险。"""
    created = _create_key(client, admin_token, "禁登自测", "system:user:list")
    account = created["name"]
    me = client.get("/api/v1/auth/me", headers=_headers(created["key"])).json()["data"]
    account = me["user"]["username"]

    response = client.post(
        "/api/v1/auth/login",
        json={"username": account, "password": "whatever"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_SERVICE_ACCOUNT_LOGIN"


def test_non_superuser_cannot_grant_scopes_it_lacks(
    client: TestClient, admin_token: str
) -> None:
    """能建 Key 不等于能签发任意权限，否则安全域白做。"""
    operator = _unwrap(
        client.post(
            "/api/v1/system/users",
            headers=_headers(admin_token),
            json={
                "username": "key_operator",
                "nickname": "Key 发放员",
                "password": "Target12345",
                "status": "active",
                "must_change_password": False,
            },
        )
    )
    # 先建一个角色，等会儿指派给操作人；建用户后立刻登录一次没有意义，删掉
    # 先给操作人一个「能进安全域、能建 Key 但没有目标权限」的角色。
    # 角色创建时不带权限，权限要单独走 /roles/{id}/permissions 分配
    role = _unwrap(
        client.post(
            "/api/v1/system/roles",
            headers=_headers(admin_token),
            json={
                "code": "key_issuer",
                "name": "Key 发放员",
                "data_scope": "self",
            },
        )
    )
    _unwrap(
        client.put(
            f"/api/v1/system/roles/{role['id']}/permissions",
            headers=_headers(admin_token),
            json={"permission_codes": ["security:api-key:create", "system:admin:access"]},
        )
    )
    _unwrap(
        client.put(
            f"/api/v1/system/users/{operator['id']}/roles",
            headers=_headers(admin_token),
            json={"role_ids": [role["id"]]},
        )
    )
    refreshed = client.post(
        "/api/v1/auth/login",
        json={"username": "key_operator", "password": "Target12345"},
    ).json()["data"]["access_token"]

    beyond = client.post(
        "/api/v1/security/api-keys",
        headers=_headers(refreshed),
        json={"name": "越权 Key", "scopes": "system:user:list"},
    )
    assert beyond.status_code == 403
    assert beyond.json()["code"] == "SECURITY_API_KEY_SCOPE_FORBIDDEN"

    # 授予自己确实持有的权限码则放行
    allowed = client.post(
        "/api/v1/security/api-keys",
        headers=_headers(refreshed),
        json={"name": "合规 Key", "scopes": "security:api-key:create"},
    )
    assert allowed.status_code == 200, allowed.text
