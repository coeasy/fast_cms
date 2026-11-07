"""P2-6：头像/文件引用完整性——删除文件后用户头像引用不悬挂。"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from tests.conftest import auth

# 1x1 透明 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8"
    "DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def test_delete_file_clears_avatar_reference(client: TestClient, admin_token: str) -> None:
    """删除被用作头像的文件后，用户 avatar_file_id 应被置空（不悬挂）。"""
    headers = auth(admin_token)

    # 上传头像 → 服务端把 avatar_file_id 指向该文件
    avatar = client.post(
        "/api/v1/auth/avatar",
        headers=headers,
        files={"file": ("avatar.png", PNG, "image/png")},
    )
    assert avatar.status_code == 200, avatar.text
    avatar_id = avatar.json()["data"]["avatar_file_id"]
    assert avatar_id is not None

    me_before = client.get("/api/v1/auth/me", headers=headers).json()["data"]["user"]
    assert me_before["avatar_file_id"] == avatar_id

    # 删除该文件
    deleted = client.delete(f"/api/v1/tool/files/{avatar_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    # 用户头像引用应被清空，而不是指向已删除的文件
    me_after = client.get("/api/v1/auth/me", headers=headers).json()["data"]["user"]
    assert me_after["avatar_file_id"] is None


def test_cleanup_orphan_files_is_observable(client: TestClient, admin_token: str) -> None:
    """孤儿文件清理任务返回可观测的删除计数，不删除仍被头像引用的文件。"""
    headers = auth(admin_token)

    avatar = client.post(
        "/api/v1/auth/avatar",
        headers=headers,
        files={"file": ("keep.png", PNG, "image/png")},
    )
    assert avatar.status_code == 200, avatar.text
    kept_avatar_id = avatar.json()["data"]["avatar_file_id"]
    assert kept_avatar_id is not None

    # 直接调用清理：仍被引用的头像不会被删
    import asyncio

    from sqlalchemy import select

    from app.db.session import async_session_factory as factory
    from app.modules.content.models import FileObject
    from app.modules.content.service import cleanup_orphan_files

    async def run() -> int:
        async with factory() as db:
            return await cleanup_orphan_files(db, days=0)

    count = asyncio.run(run())
    assert count == 0, "被头像引用的文件不应被孤儿清理删除"

    async def still_exists() -> bool:
        async with factory() as db:
            row = (await db.execute(select(FileObject.id).where(FileObject.id == kept_avatar_id))).scalar_one_or_none()
        return row is not None

    assert asyncio.run(still_exists()) is True
