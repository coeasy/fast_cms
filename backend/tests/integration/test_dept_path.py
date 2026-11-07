"""P1-7：部门物化路径长度/深度校验 + 子树移动 + 前缀匹配后代解析。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.modules.sys.system.services import _common as service_common
from tests.conftest import auth


def _create_dept(client: TestClient, headers: dict, name: str, parent_id: int | None = None) -> dict:
    payload: dict = {"name": name, "sort": 0}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    resp = client.post("/api/v1/system/depts", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _dept_ancestors(dept_id: int) -> str:
    async def get() -> str:
        from sqlalchemy import select

        from app.db.session import async_session_factory as factory
        from app.kernel.models import Dept

        async with factory() as db:
            return (await db.execute(select(Dept.ancestors).where(Dept.id == dept_id))).scalar_one()

    return asyncio.run(get())


def test_create_chain_within_limits(client: TestClient, admin_token: str) -> None:
    """浅层部门链可正常创建，ancestors 物化路径符合预期。"""
    headers = auth(admin_token)
    a = _create_dept(client, headers, "P17-A")
    b = _create_dept(client, headers, "P17-B", parent_id=a["id"])
    c = _create_dept(client, headers, "P17-C", parent_id=b["id"])

    assert a["ancestors"] == "/"
    assert b["ancestors"] == f"/{a['id']}/"
    assert c["ancestors"] == f"/{a['id']}/{b['id']}/"


def test_dept_depth_overflow_rejected(client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """超过最大深度时返回明确业务错误 SYSTEM_DEPT_TOO_DEEP，而非 DB 截断。"""
    # 压小深度上限，避免上百次 HTTP 调用
    monkeypatch.setattr(service_common, "MAX_DEPT_DEPTH", 3)
    headers = auth(admin_token)
    a = _create_dept(client, headers, "P17-D1")
    b = _create_dept(client, headers, "P17-D2", parent_id=a["id"])
    c = _create_dept(client, headers, "P17-D3", parent_id=b["id"])
    # 第 4 层：ancestors 斜杠数 4 > 3 → 拒绝
    resp = client.post("/api/v1/system/depts", json={"name": "P17-D4", "parent_id": c["id"], "sort": 0}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == "SYSTEM_DEPT_TOO_DEEP"


def test_move_subtree_updates_descendant_paths(client: TestClient, admin_token: str) -> None:
    """把子树挂到新的父节点后，整棵子树 ancestors 前缀同步平移。"""
    headers = auth(admin_token)
    a = _create_dept(client, headers, "P17-M1")
    b = _create_dept(client, headers, "P17-M2", parent_id=a["id"])
    c = _create_dept(client, headers, "P17-M3", parent_id=b["id"])
    d = _create_dept(client, headers, "P17-M4", parent_id=c["id"])

    # 把 C（含其子 D）从 B 之下移到 A 之下
    resp = client.put(f"/api/v1/system/depts/{c['id']}", json={"parent_id": a["id"]}, headers=headers)
    assert resp.status_code == 200, resp.text

    assert _dept_ancestors(c["id"]) == f"/{a['id']}/"
    assert _dept_ancestors(d["id"]) == f"/{a['id']}/{c['id']}/"


def test_cycle_guard_on_move(client: TestClient, admin_token: str) -> None:
    """把父节点挂到自己的后代下应被拒绝（SYSTEM_DEPT_CYCLE）。"""
    headers = auth(admin_token)
    a = _create_dept(client, headers, "P17-C1")
    b = _create_dept(client, headers, "P17-C2", parent_id=a["id"])
    resp = client.put(f"/api/v1/system/depts/{a['id']}", json={"parent_id": b["id"]}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == "SYSTEM_DEPT_CYCLE"
