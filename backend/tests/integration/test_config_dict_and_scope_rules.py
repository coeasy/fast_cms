from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import auth


def _unwrap(response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_config_value_matches_declared_type(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    invalid = client.post(
        "/api/v1/system/configs",
        headers=headers,
        json={"key": "test.numeric_flag", "name": "数值参数", "value": "abc", "type": "int"},
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["code"] == "CONFIG_VALUE_INVALID"

    created = _unwrap(
        client.post(
            "/api/v1/system/configs",
            headers=headers,
            json={
                "key": "test.numeric_flag",
                "name": "数值参数",
                "value": "12",
                "type": "int",
                "is_public": True,
            },
        )
    )
    public = _unwrap(client.get("/api/v1/system/public-config"))
    assert public["test.numeric_flag"] == 12

    broken = client.put(
        f"/api/v1/system/configs/{created['id']}",
        headers=headers,
        json={"value": "{not-json", "type": "json"},
    )
    assert broken.status_code == 422, broken.text
    assert broken.json()["code"] == "CONFIG_VALUE_INVALID"
    # 校验失败不能把旧值改掉
    listed = _unwrap(client.get("/api/v1/system/configs", params={"keyword": "test.numeric_flag"}, headers=headers))
    assert listed[0]["value"] == "12"

    assert client.delete(f"/api/v1/system/configs/{created['id']}", headers=headers).status_code == 200


def test_dict_cache_reflects_item_and_type_deletion(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    dict_type = _unwrap(
        client.post(
            "/api/v1/system/dict-types",
            headers=headers,
            json={"name": "缓存验证字典", "code": "test_cache_dict"},
        )
    )
    item = _unwrap(
        client.post(
            f"/api/v1/system/dict-types/{dict_type['id']}/items",
            headers=headers,
            json={"label": "启用", "value": "on"},
        )
    )
    assert [row["value"] for row in _unwrap(client.get("/api/v1/system/dicts/test_cache_dict", headers=headers))["items"]] == ["on"]

    assert client.delete(f"/api/v1/system/dict-items/{item['id']}", headers=headers).status_code == 200
    assert _unwrap(client.get("/api/v1/system/dicts/test_cache_dict", headers=headers))["items"] == []

    assert client.delete(f"/api/v1/system/dict-types/{dict_type['id']}", headers=headers).status_code == 200
    missing = client.get("/api/v1/system/dicts/test_cache_dict", headers=headers)
    assert missing.status_code == 404, missing.text


def test_dept_referenced_by_role_scope_cannot_be_deleted(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    dept = _unwrap(client.post("/api/v1/system/depts", headers=headers, json={"name": "范围验证部门"}))
    role = _unwrap(
        client.post(
            "/api/v1/system/roles",
            headers=headers,
            json={"name": "范围验证角色", "code": "test_scope_role", "data_scope": "custom"},
        )
    )
    assigned = client.put(
        f"/api/v1/system/roles/{role['id']}/data-scope",
        headers=headers,
        json={"data_scope": "custom", "dept_ids": [dept["id"]]},
    )
    assert assigned.status_code == 200, assigned.text

    blocked = client.delete(f"/api/v1/system/depts/{dept['id']}", headers=headers)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "SYSTEM_DEPT_IN_USE"

    reset = client.put(
        f"/api/v1/system/roles/{role['id']}/data-scope",
        headers=headers,
        json={"data_scope": "self", "dept_ids": []},
    )
    assert reset.status_code == 200, reset.text
    assert client.delete(f"/api/v1/system/depts/{dept['id']}", headers=headers).status_code == 200
    assert client.delete(f"/api/v1/system/roles/{role['id']}", headers=headers).status_code == 200


def test_menu_parent_cycle_is_rejected_without_looping(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    parent = _unwrap(
        client.post(
            "/api/v1/system/menus",
            headers=headers,
            json={"name": "循环父级", "type": "directory", "path": "/test-cycle"},
        )
    )
    child = _unwrap(
        client.post(
            "/api/v1/system/menus",
            headers=headers,
            json={"name": "循环子级", "type": "directory", "path": "/test-cycle/child", "parent_id": parent["id"]},
        )
    )
    cycle = client.put(
        f"/api/v1/system/menus/{parent['id']}",
        headers=headers,
        json={"parent_id": child["id"]},
    )
    assert cycle.status_code == 422, cycle.text
    assert cycle.json()["code"] == "SYSTEM_MENU_CYCLE"

    assert client.delete(f"/api/v1/system/menus/{child['id']}", headers=headers).status_code == 200
    assert client.delete(f"/api/v1/system/menus/{parent['id']}", headers=headers).status_code == 200


def test_menu_paths_cannot_escape_the_app(client: TestClient, admin_token: str) -> None:
    """菜单 path 会渲染成侧边栏的 <a href>，脚本协议和外站地址必须挡在接口层。"""
    headers = auth(admin_token)
    for bad in ("javascript:alert(1)", "//evil.example/path", "https://evil.example", "/demo?next=//evil"):
        response = client.post(
            "/api/v1/system/menus",
            headers=headers,
            json={"name": "外链菜单", "type": "menu", "path": bad},
        )
        assert response.status_code == 422, f"{bad} 应当被拒绝：{response.text}"

    created = _unwrap(
        client.post(
            "/api/v1/system/menus",
            headers=headers,
            json={"name": "站内菜单", "type": "menu", "path": "/custom/inner-page"},
        )
    )
    # 更新也要校验，否则可以先建合法菜单再改成脚本入口
    escaped = client.put(
        f"/api/v1/system/menus/{created['id']}",
        headers=headers,
        json={"path": "javascript:alert(1)"},
    )
    assert escaped.status_code == 422, escaped.text
    assert client.delete(f"/api/v1/system/menus/{created['id']}", headers=headers).status_code == 200


def _find_menu(nodes: list[dict], predicate) -> dict | None:
    for node in nodes:
        if predicate(node):
            return node
        found = _find_menu(node.get("children") or [], predicate)
        if found is not None:
            return found
    return None


def test_menu_delete_outcomes_match_frontend_copy(client: TestClient, admin_token: str) -> None:
    """删除菜单有三种结果，前端的确认文案与提示全靠返回值区分，这里把契约钉住。"""
    headers = auth(admin_token)
    builtin = _find_menu(_unwrap(client.get("/api/v1/system/menus", headers=headers)), lambda node: node["is_builtin"])
    assert builtin is not None, "基线数据里应当存在内置菜单"
    refused = client.delete(f"/api/v1/system/menus/{builtin['id']}", headers=headers)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "SYSTEM_MENU_BUILTIN"

    plain = _unwrap(
        client.post(
            "/api/v1/system/menus",
            headers=headers,
            json={"name": "无权限菜单", "type": "menu", "path": "/custom/plain-probe"},
        )
    )
    assert _unwrap(client.delete(f"/api/v1/system/menus/{plain['id']}", headers=headers)) == {"deleted": True}

    guarded = _unwrap(
        client.post(
            "/api/v1/system/menus",
            headers=headers,
            json={
                "name": "带权限菜单",
                "type": "menu",
                "path": "/custom/guarded-probe",
                "permission_code": "dashboard:view",
            },
        )
    )
    assert _unwrap(client.delete(f"/api/v1/system/menus/{guarded['id']}", headers=headers)) == {"disabled": True}
    still_there = _find_menu(_unwrap(client.get("/api/v1/system/menus", headers=headers)), lambda node: node["id"] == guarded["id"])
    assert still_there is not None and still_there["status"] == "disabled"
    # 解绑权限码才能真正删除，否则测试库里会留下停用菜单
    _unwrap(client.put(f"/api/v1/system/menus/{guarded['id']}", headers=headers, json={"permission_code": None}))
    assert _unwrap(client.delete(f"/api/v1/system/menus/{guarded['id']}", headers=headers)) == {"deleted": True}


def test_notify_record_filters_use_model_enums(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    bad_status = client.get("/api/v1/notify/records", params={"status": "queued"}, headers=headers)
    assert bad_status.status_code == 422, bad_status.text
    bad_event = client.get("/api/v1/notify/records", params={"event": "job"}, headers=headers)
    assert bad_event.status_code == 422, bad_event.text
    for status in ("pending", "sending", "success", "failed", "skipped"):
        assert client.get("/api/v1/notify/records", params={"status": status}, headers=headers).status_code == 200
