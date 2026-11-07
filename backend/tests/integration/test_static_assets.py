"""静态资源版本化与缓存策略回归测试（P0-6）。

验收点：
- 页面引用的 css/js 都带 ``?v=`` 且版本号一致；
- 带 ``v=`` 的入口资源返回 ``immutable`` 长缓存；
- 不带 ``v=`` 的动态子模块（如 ``./core.js``）返回 ``no-cache``，配合 ETag 每次协商，
  保证「升级后子模块也新鲜」；
- 任何静态文件变动（内容或 mtime）都会改变版本号。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import STATIC_DIR
from app.web import assets


def _asset_refs(html: str) -> dict[str, str]:
    """返回 {资源相对路径: 版本号}，例如 {'static/css/base.css': 'abc123'}。"""
    return dict(re.findall(r"(static/[A-Za-z0-9_./-]+\.(?:css|js))\?v=([0-9a-f]+)", html))


def test_page_references_assets_with_consistent_version(client: TestClient) -> None:
    body = client.get(f"{settings.admin_base}/dashboard").text
    refs = _asset_refs(body)
    # 三个入口（base.css / app.js / admin.css）+ theme-boot 至少要出现
    assert "static/css/base.css" in refs
    assert "static/js/app.js" in refs
    assert "static/js/theme-boot.js" in refs
    assert len(set(refs.values())) == 1, f"版本号必须一致：{refs}"
    assert re.fullmatch(r"[0-9a-f]{10}", next(iter(refs.values())))


def test_versioned_asset_is_immutable(client: TestClient) -> None:
    version = next(iter(_asset_refs(client.get(f"{settings.admin_base}/dashboard").text).values()))
    response = client.get(f"{settings.admin_base}/static/css/base.css?v={version}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_unversioned_submodule_revalidates(client: TestClient) -> None:
    """JS 内部相对 import 不带查询串：必须 no-cache + ETag，否则升级后拉旧模块。"""
    response = client.get(f"{settings.admin_base}/static/js/core.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers.get("etag"), "no-cache 必须配 ETag/Last-Modified 才能协商"


def test_asset_version_changes_on_file_change() -> None:
    """新增/删除一个静态文件就会改变版本号 —— P0-6 的验收场景。

    用临时占位文件而不是改动真实资源，测试跑完目录内容与原先完全一致，不需要还原 mtime。
    """
    target = STATIC_DIR / ".cache-bust-probe"
    assets.reset_cache()
    before = assets.asset_version()
    try:
        target.write_text("cache bust probe\n", encoding="utf-8")
        assets.reset_cache()
        assert assets.asset_version() != before, "新增文件后版本号应当变化"
        target.unlink()
        assets.reset_cache()
        assert assets.asset_version() == before, "删除文件后版本号应当回到原值"
    finally:
        target.unlink(missing_ok=True)


def test_asset_version_changes_on_app_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """升级版本号也会改变资源指纹，保证发布后 URL 必然变化。"""
    before = assets.asset_version()
    monkeypatch.setattr(settings, "app_version", "99.99.99")
    assets.reset_cache()
    after = assets.asset_version()
    assert after != before


def test_assets_served_without_breaking_lookup(client: TestClient) -> None:
    """同 URL 带不带版本号都应命中同一文件（查询串不参与文件匹配）。"""
    version = next(iter(_asset_refs(client.get(f"{settings.admin_base}/dashboard").text).values()))
    assert client.get(f"{settings.admin_base}/static/css/base.css?v={version}").status_code == 200
    assert client.get(f"{settings.admin_base}/static/css/base.css?v=deadbeef00").status_code == 200
