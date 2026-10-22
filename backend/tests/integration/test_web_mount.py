"""HTML 前端挂载回归：页面由后端渲染，静态资源与页面路由都挂在各自的 base 下。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.main import STATIC_DIR, create_app
from app.web.router import PAGES, WEB_DIR, base_path, router_prefix


def test_site_base_normalization() -> None:
    assert Settings(admin_base="admin/").admin_base == "/admin"
    assert Settings(admin_base="/console").admin_base == "/console"
    assert Settings(admin_base=" ").admin_base == "/admin"
    # 老部署形态：后台继续占根路径
    assert Settings(admin_base="/").admin_base == "/"
    assert Settings(portal_base="front/").portal_base == "/front"
    assert Settings(portal_base="/").portal_base == "/"
    with pytest.raises(ValidationError, match="ADMIN_BASE"):
        Settings(admin_base="/C:/Program Files/Git/admin")
    with pytest.raises(ValidationError, match="不能相同"):
        Settings(admin_base="/site", portal_base="/site")
    # 挂载点压到 API 或后端保留路径上会静默吞掉接口/文档，必须直接拒绝
    with pytest.raises(ValidationError, match="API_PREFIX"):
        Settings(portal_base="/api")
    with pytest.raises(ValidationError, match="保留路径"):
        Settings(portal_base="/docs")


def test_base_helpers_keep_root_form() -> None:
    """带尾斜杠形态用于拼 URL，router prefix 用空串表示根，否则会拼出 //path。"""
    assert base_path("/admin") == "/admin/"
    assert base_path("admin") == "/admin/"
    assert base_path("/") == "/"
    assert router_prefix("/admin") == "/admin"
    assert router_prefix("/") == ""
    assert router_prefix("") == ""


def test_static_dir_ships_with_backend() -> None:
    """静态资源是后端源码的一部分，缺了它页面会整块空白。"""
    # 样式分三层：base 是组件层，admin / portal / public 是各自外壳的骨架层
    for name in ("base.css", "admin.css", "portal.css", "public.css"):
        assert (STATIC_DIR / "css" / name).is_file(), name
    assert (STATIC_DIR / "js" / "app.js").is_file()
    assert (STATIC_DIR / "js" / "core.js").is_file()
    # 外壳分三套：后台 / 登录页 / 前台首页
    for name in ("admin.js", "auth.js", "portal.js"):
        assert (STATIC_DIR / "js" / "shell" / name).is_file(), name


def test_component_library_and_layouts_exist() -> None:
    """组件库与三套外壳是页面模板的公共底座。"""
    for name in ("components/ui.html", "layouts/admin.html", "layouts/portal.html", "layouts/blank.html"):
        assert (WEB_DIR / "templates" / name).is_file(), name


def test_portal_page_does_not_load_admin_assets(client: TestClient) -> None:
    """前后台彻底分离：前台页面不引用后台样式与外壳脚本。"""
    body = client.get("/front/").text
    assert "static/css/portal.css" in body
    assert "static/css/base.css" in body
    assert "static/css/admin.css" not in body
    assert "shell/admin.js" not in body


def test_admin_page_does_not_load_portal_assets(client: TestClient) -> None:
    body = client.get("/admin/dashboard").text
    assert "static/css/admin.css" in body
    assert "static/css/portal.css" not in body


def test_every_page_has_template_and_script() -> None:
    for page in PAGES:
        template = WEB_DIR / "templates" / str(page["template"])
        assert template.is_file(), page["template"]
        if page["page"] in {"login", "error", "portal/home"}:
            continue
        assert (STATIC_DIR / "js" / "pages" / f"{page['page']}.js").is_file(), page["page"]


def test_admin_pages_mount_under_admin_base(client: TestClient) -> None:
    assert settings.admin_base == "/admin"
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # 页面把 base / apiBase 写进 #fastcms-config JSON 数据块（不是内联脚本，
    # 这样 CSP 才能禁用 unsafe-inline），前端 core.js 读取它拼请求与资源地址
    assert '"base": "/admin/"' in body
    assert f'"apiBase": "{settings.api_prefix}"' in body
    assert 'id="fastcms-config"' in body
    assert "/admin/static/js/app.js" in body
    # 首屏主题必须是外部脚本，内联脚本会让 CSP 失效
    assert 'src="/admin/static/js/theme-boot.js"' in body
    assert "<script>\n" not in body
    # 静态资源同源同端口
    assert client.get("/admin/static/css/base.css").status_code == 200
    assert client.get("/admin/static/css/admin.css").status_code == 200
    assert client.get("/admin/static/js/core.js").status_code == 200


def test_admin_root_redirects_to_dashboard(client: TestClient) -> None:
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/dashboard"


def test_admin_unknown_page_renders_html_404(client: TestClient) -> None:
    """未知路径不能再落到 JSON 错误页，否则用户看到一屏 JSON。"""
    response = client.get("/admin/nope/here")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "页面不存在" in response.text


def test_portal_mounts_under_portal_base(client: TestClient) -> None:
    assert settings.portal_base == "/front"
    response = client.get("/front/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '"base": "/front/"' in response.text
    # 前台 404 同样是 HTML，且不会吞掉后台
    assert client.get("/front/nope").status_code == 404
    assert client.get("/admin/dashboard").status_code == 200


def test_root_redirects_to_portal(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/front/"


def test_api_prefix_still_wins_over_page_catch_all(client: TestClient) -> None:
    """后台的 catch-all 页面路由不能把未知 API 路径变成 200 HTML。"""
    response = client.get(f"{settings.api_prefix}/definitely-not-an-endpoint")
    assert response.status_code == 404
    assert "application/json" in response.headers["content-type"]
    assert client.get("/healthz").json()["status"] == "ok"


def test_admin_base_root_keeps_pages_at_root(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(settings, "admin_base", "/")
    scoped = TestClient(create_app())
    try:
        root = scoped.get("/", follow_redirects=False)
        assert root.status_code == 307
        assert root.headers["location"] == "/dashboard"
        assert scoped.get("/dashboard").status_code == 200
        # 后台占根路径时前台不挂载，避免两个 catch-all 互相抢路由
        assert scoped.get("/front/").status_code == 404
    finally:
        scoped.close()
