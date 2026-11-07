"""P2-10：前台（公开站点）读取已发布公告——匿名可读，不泄露已读状态。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth


def _publish_notice(client: TestClient, admin_token: str, title: str) -> int:
    headers = auth(admin_token)
    created = client.post(
        "/api/v1/content/notices",
        headers=headers,
        json={"title": title, "content": "公告正文：这是一条前台可见的公开内容。"},
    )
    assert created.status_code == 200, created.text
    notice_id = created.json()["data"]["id"]
    published = client.post(f"/api/v1/content/notices/{notice_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return notice_id


def test_published_notices_anonymous_readable(client: TestClient, admin_token: str) -> None:
    notice_id = _publish_notice(client, admin_token, "匿名可读公告")

    # 列表：匿名（无 token）可读，且 read 恒为 False（匿名无已读状态）
    listed = client.get("/api/v1/content/notices/published")
    assert listed.status_code == 200, listed.text
    items = listed.json()["data"]["items"]
    assert any(item["id"] == notice_id for item in items)
    matched = next(item for item in items if item["id"] == notice_id)
    assert matched["read"] is False

    # 详情：匿名可读
    detail = client.get(f"/api/v1/content/notices/{notice_id}/published")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["title"] == "匿名可读公告"
    assert detail.json()["data"]["read"] is False

    # 草稿/不存在：匿名同样 404，不泄露未发布内容
    missing = client.get("/api/v1/content/notices/999999/published")
    assert missing.status_code == 404
    assert missing.json()["code"] == "CONTENT_NOTICE_NOT_FOUND"


def test_published_notices_optional_auth_still_works_with_login(
    client: TestClient, admin_token: str
) -> None:
    """带 token 访问同一接口行为不变：已读状态照常计算。"""
    notice_id = _publish_notice(client, admin_token, "登录态公告")
    headers = auth(admin_token)

    read = client.post(f"/api/v1/content/notices/{notice_id}/read", headers=headers)
    assert read.status_code == 200

    detail = client.get(f"/api/v1/content/notices/{notice_id}/published", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["read"] is True
