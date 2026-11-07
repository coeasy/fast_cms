"""P1-5：total=false 契约 + keyset 分页（大表降本）集成测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth


def test_total_false_omits_total_field(client: TestClient, admin_token: str) -> None:
    """total=false 时列表响应不携带 total 字段。"""
    headers = auth(admin_token)
    resp = client.get("/api/v1/system/users", params={"page": 1, "page_size": 5, "total": False}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "total" not in data
    assert "items" in data
    assert data["page"] == 1


def test_total_true_default_includes_total_field(client: TestClient, admin_token: str) -> None:
    """默认（不传 total）仍返回 total，保持向后兼容。"""
    headers = auth(admin_token)
    resp = client.get("/api/v1/system/users", params={"page": 1, "page_size": 5}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data["total"], int)
    assert data["total"] >= 1


def test_audit_logs_total_false(client: TestClient, admin_token: str) -> None:
    """大表（操作日志）同样支持 total=false，契约一致。"""
    headers = auth(admin_token)
    resp = client.get("/api/v1/monitor/operation-logs", params={"page": 1, "page_size": 5, "total": False}, headers=headers)
    assert resp.status_code == 200
    assert "total" not in resp.json()["data"]


def test_keyset_pagination_walks_pages(client: TestClient, admin_token: str) -> None:
    """keyset 分页：首页无游标、按 key 升序、末页 next_cursor 为 None。"""
    from sqlalchemy import select

    from app.core.pagination import keyset_paginate
    from app.db.session import async_session_factory as factory
    from app.kernel.models import User

    ids: list[int] = []

    async def walk() -> tuple[list[int], int]:
        cursor = None
        pages = 0
        while True:
            async with factory() as db:
                rows, next_cursor = await keyset_paginate(
                    db,
                    select(User),
                    key_col=User.id,
                    cursor=cursor,
                    limit=2,
                    ascending=True,
                )
            for row in rows:
                ids.append(row.id)
            pages += 1
            if next_cursor is None or pages > 50:  # 安全上限，防死循环
                break
            cursor = next_cursor
        return ids, pages

    import asyncio

    ids, pages = asyncio.run(walk())
    assert ids, "keyset 分页至少返回一页"
    assert ids == sorted(ids), "keyset 分页结果必须按 key 单调递增"
    assert pages >= 1
    # 末页 next_cursor 为 None 时停止，不重复返回
    assert len(ids) == len(set(ids))
