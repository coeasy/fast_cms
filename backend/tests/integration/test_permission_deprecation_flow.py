"""6-2 端到端：废弃权限的宽限期必须对「所有」鉴权路径生效。

单测只验证 ``require_permission`` 的四条分支，这里补上真实链路，覆盖两个
容易写错的点：

1. 宽限期内权限仍算「持有」——``/auth/me`` 要照常下发，菜单和 ``data-perm``
   按钮才不会静默消失；否则宽限期只剩 API 层看得见，Web 界面等于提前下线。
2. 响应头必须真的出现在 HTTP 响应上（``Deprecation`` / ``Sunset``），而不是
   只挂在依赖注入拿到的那个 ``Response`` 对象上。

过期后则相反：``/auth/me`` 不再下发该码，接口返回 ``AUTH_PERMISSION_SUNSET``。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bootstrap.permissions import ADMIN_PERMISSION
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import session_scope
from app.kernel.models import Permission, Role, User

API = settings.api_prefix
CODE = "system:theme:list"
USERNAME = "deprecation_probe"
PASSWORD = "Probe123456"


async def _prepare() -> None:
    """建一个只持有「后台访问 + 主题列表」的普通账号。"""
    async with session_scope() as db:
        perms = (
            (await db.execute(select(Permission).where(Permission.code.in_([CODE, ADMIN_PERMISSION]))))
            .scalars()
            .all()
        )
        role = Role(
            name="废弃权限探针",
            code="deprecation-probe",
            status="active",
            data_scope="self",
            remark="integration test",
        )
        role.permissions = list(perms)
        user = User(
            username=USERNAME,
            username_normalized=USERNAME.lower(),
            nickname="探针",
            password_hash=hash_password(PASSWORD),
            status="active",
            must_change_password=False,
        )
        user.roles = [role]
        db.add_all([role, user])


async def _deprecate(at: datetime | None) -> None:
    async with session_scope() as db:
        perm = (await db.execute(select(Permission).where(Permission.code == CODE))).scalars().one()
        perm.deprecated_at = at


def _login(client: TestClient) -> dict[str, str]:
    resp = client.post(f"{API}/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


async def test_deprecated_permission_stays_usable_during_grace(client: TestClient) -> None:
    await _prepare()
    await _deprecate(datetime.now(UTC) - timedelta(days=1))

    headers = _login(client)

    me = client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200
    payload = me.json()["data"]
    # 宽限期内仍下发：菜单过滤与 data-perm 按钮据此判断，摘掉就会静默少入口
    assert CODE in payload["permissions"]
    assert any(node.get("path") == "/system/theme" for node in _flatten(payload["menus"]))

    listed = client.get(f"{API}/system/themes", headers=headers)
    assert listed.status_code == 200
    assert listed.headers.get("Deprecation") == "true"
    assert listed.headers.get("Sunset", "").endswith("GMT")


async def test_deprecated_permission_is_revoked_after_sunset(client: TestClient) -> None:
    await _prepare()
    past = datetime.now(UTC) - timedelta(days=settings.permission_deprecation_grace_days + 1)
    await _deprecate(past)

    headers = _login(client)

    me = client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200
    payload = me.json()["data"]
    assert CODE not in payload["permissions"]
    assert not any(node.get("path") == "/system/theme" for node in _flatten(payload["menus"]))

    listed = client.get(f"{API}/system/themes", headers=headers)
    assert listed.status_code == 403
    assert listed.json()["code"] == "AUTH_PERMISSION_SUNSET"
    assert "Sunset" not in listed.headers


def _flatten(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten(node.get("children") or []))
    return out
