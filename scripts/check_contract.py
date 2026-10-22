"""OpenAPI 契约与权限覆盖检查，供 CI / make contract-check 使用。

前端是后端直接渲染的 Jinja2 页面 + 原生 ES Module，
因此「路由 / 页面脚本」两边都要和后端注册的菜单对得上：
菜单里有的路径必须能打开页面，页面脚本必须存在，孤儿页面也要报出来。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WEB = BACKEND / "app" / "web"
PAGES_JS = WEB / "static" / "js" / "pages"
sys.path.insert(0, str(BACKEND))

from app.bootstrap.menus import MENU_TREE  # noqa: E402
from app.bootstrap.permissions import PERMISSIONS  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.web.router import PAGES, templates  # noqa: E402

# 免鉴权路径写“前缀无关”的形式，运行时再拼上 API_PREFIX。
PUBLIC_API_PATHS = (
    "/meta/version",
    "/auth/login",
    "/auth/refresh",
    "/auth/logout",
    "/auth/me",
    "/auth/heartbeat",
    "/auth/change-password",
    "/auth/sessions",
    "/auth/sessions/{session_id}",
    "/auth/profile",
    "/auth/avatar",
    "/system/depts/options",
    "/system/public-config",
    "/system/themes/public",
    "/content/notices/published",
    "/content/notices/unread-count",
    "/content/notices/read-all",
    "/content/notices/{notice_id}/published",
    "/content/notices/{notice_id}/read",
)

# 健康检查不走 API 前缀，也不做鉴权。
UNPREFIXED_PATHS = {"/healthz", "/readyz"}

PUBLIC_PATHS = {*UNPREFIXED_PATHS, *(settings.api_prefix + path for path in PUBLIC_API_PATHS)}

# 登录、异常页和个人中心本来就不该出现在侧边栏里
NON_MENU_ROUTES = {"/login", "/403", "/404", "/account/profile", "/account/password"}


def collect_menu_permissions(nodes: list[dict]) -> set[str]:
    codes: set[str] = set()
    for node in nodes:
        if node.get("permission_code"):
            codes.add(str(node["permission_code"]))
        codes.update(collect_menu_permissions(node.get("children") or []))
    return codes


def collect_component_keys(nodes: list[dict]) -> list[str]:
    keys: list[str] = []
    for node in nodes:
        if node.get("component_key"):
            keys.append(str(node["component_key"]))
        keys.extend(collect_component_keys(node.get("children") or []))
    return keys


def collect_menu_paths(nodes: list[dict]) -> list[str]:
    """目录节点只是分组，真正需要页面路由的是叶子菜单。"""
    paths: list[str] = []
    for node in nodes:
        children = node.get("children") or []
        if node.get("path") and str(node.get("type", "")) != "directory" and not children:
            paths.append(str(node["path"]))
        paths.extend(collect_menu_paths(children))
    return paths


def check_routes(menu_paths: list[str], errors: list[str]) -> None:
    """菜单和页面各自检查不够，还要成对：菜单点进去 404、页面没有入口都是断链。"""
    declared = {str(page["path"]) for page in PAGES}
    for path in sorted(set(menu_paths) - declared):
        errors.append(f"菜单路径 {path} 没有对应的后端页面路由，点击会落到 404")
    for path in sorted(declared - set(menu_paths) - NON_MENU_ROUTES):
        errors.append(f"页面路由 {path} 没有菜单入口，属于孤儿页面")


def check_page_scripts(errors: list[str]) -> None:
    """每个页面都必须有对应的模板，且（登录/错误页除外）有同名页面脚本。"""
    for page in PAGES:
        template = WEB / "templates" / str(page["template"])
        if not template.exists():
            errors.append(f"页面 {page['path']} 缺少模板：{page['template']}")
        key = str(page["page"])
        if key in {"login", "error", "portal/home"}:
            continue
        if not (PAGES_JS / f"{key}.js").exists():
            errors.append(f"页面 {page['path']} 缺少脚本：static/js/pages/{key}.js")


# 页面只能挂在这三套外壳上：后台 / 前台 / 公共页（登录、错误页）。
# 前台不能继承后台外壳，否则会把侧边栏、登录守卫和 admin.css 一起带进来。
ADMIN_LAYOUT = "layouts/admin.html"
PORTAL_LAYOUT = "layouts/portal.html"
BLANK_LAYOUT = "layouts/blank.html"
PUBLIC_PAGE_KEYS = {"login", "error"}


def expected_layout(page: dict) -> str:
    key = str(page["page"])
    if key == "portal/home":
        return PORTAL_LAYOUT
    if key in PUBLIC_PAGE_KEYS:
        return BLANK_LAYOUT
    return ADMIN_LAYOUT


def check_page_layouts(errors: list[str]) -> None:
    """模板继承关系是前后台分离的硬约束，靠人肉 review 迟早会串。"""
    for page in PAGES:
        path = WEB / "templates" / str(page["template"])
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        want = expected_layout(page)
        if f'{{% extends "{want}" %}}' not in text:
            errors.append(f"页面 {page['path']} 应继承 {want}")
        for other in (ADMIN_LAYOUT, PORTAL_LAYOUT, BLANK_LAYOUT):
            if other != want and f'{{% extends "{other}" %}}' in text:
                errors.append(f"页面 {page['path']} 同时继承了 {other}")

    portal_home = WEB / "templates" / "portal" / "home.html"
    if portal_home.exists() and f'{{% extends "{PORTAL_LAYOUT}" %}}' not in portal_home.read_text(encoding="utf-8"):
        errors.append(f"前台首页应继承 {PORTAL_LAYOUT}")

    base = WEB / "templates" / "base.html"
    if base.exists() and "static/css/base.css" not in base.read_text(encoding="utf-8"):
        errors.append("base.html 应只加载 static/css/base.css，骨架样式由 layouts/* 追加")


def check_component_keys(errors: list[str]) -> None:
    for component_key in collect_component_keys(MENU_TREE):
        if not (PAGES_JS / f"{component_key}.js").exists():
            errors.append(f"菜单组件未注册：static/js/pages/{component_key}.js")


# 页面脚本按 id 取节点，模板改结构时最容易漏掉；这里把两边对一遍。
ID_IN_JS = re.compile(r"""\$\(\s*['"]#([A-Za-z][\w-]*)['"]""")
QUERY_IN_JS = re.compile(r"""querySelector(?:All)?\(\s*['"]#([A-Za-z][\w-]*)['"]""")
ID_IN_HTML = re.compile(r'id="([^"]+)"')
MODAL_IN_HTML = re.compile(r"""data-modal-(?:open|close)=["']([A-Za-z][\w-]*)["']""")

SHELL_TEMPLATES = {
    "pages/login.html": "shell/auth.js",
    "portal/home.html": "shell/portal.js",
}

RENDER_CONTEXT = {
    "base": "/admin/",
    "admin_base": "/admin/",
    "portal_base": "/front/",
    "api_prefix": "/api/v1",
    "app_name": "Fast CMS",
    "app_version": "0.0.0",
    "page_title": "T",
    "page_subtitle": "",
    "page_path": "/",
    "page_key": "x",
    "code": "",
    "message": "",
    "detail": "",
}


def render_page(template: str, **overrides: object) -> str:
    return templates.env.get_template(template).render({**RENDER_CONTEXT, **overrides})


def check_dom_ids(errors: list[str]) -> None:
    """页面脚本里的 #id 选择器必须能在渲染后的模板里找到，反之弹层指向也要存在。"""
    for page in PAGES:
        try:
            html = render_page(
                str(page["template"]),
                page_title=page["title"],
                page_subtitle=page.get("subtitle", ""),
                page_path=page["path"],
                page_key=page["page"],
            )
        except Exception as exc:  # noqa: BLE001 - 模板语法错误也要当成契约失败
            errors.append(f"页面 {page['path']} 渲染失败：{exc}")
            continue
        ids = set(ID_IN_HTML.findall(html))
        for modal_id in set(MODAL_IN_HTML.findall(html)) - ids:
            errors.append(f"页面 {page['path']} 的弹层指向不存在的 #{modal_id}")
        key = str(page["page"])
        if key in {"login", "error", "portal/home"}:
            continue
        script = PAGES_JS / f"{key}.js"
        if not script.exists():
            continue
        source = script.read_text(encoding="utf-8")
        for node_id in sorted(set(ID_IN_JS.findall(source)) | set(QUERY_IN_JS.findall(source))):
            if node_id not in ids:
                errors.append(f"页面脚本 {key}.js 引用了模板中不存在的 #{node_id}")

    for template, script in SHELL_TEMPLATES.items():
        html = render_page(template)
        ids = set(ID_IN_HTML.findall(html))
        source = (WEB / "static" / "js" / script).read_text(encoding="utf-8")
        for node_id in sorted(set(ID_IN_JS.findall(source)) | set(QUERY_IN_JS.findall(source))):
            if node_id not in ids:
                errors.append(f"外壳脚本 {script} 引用了 {template} 中不存在的 #{node_id}")


def check_prefix(spec_paths: list[str], errors: list[str]) -> None:
    """API 前缀改动后必须重新导出 OpenAPI，否则契约文件与运行时不一致。"""
    prefix = settings.api_prefix
    for path in spec_paths:
        if path in UNPREFIXED_PATHS:
            continue
        if not path.startswith(f"{prefix}/"):
            errors.append(f"OpenAPI 路径 {path} 不在当前 API_PREFIX={prefix} 下，请重新执行 export-openapi")


def main() -> int:
    spec = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    registry = {item["code"] for item in PERMISSIONS}
    errors: list[str] = []
    operation_ids: set[str] = set()
    api_permissions: set[str] = set()

    check_prefix(list(spec.get("paths", {})), errors)
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            operation_id = operation.get("operationId")
            if not operation_id:
                errors.append(f"{method.upper()} {path} 缺少 operationId")
            elif operation_id in operation_ids:
                errors.append(f"operationId 重复：{operation_id}")
            else:
                operation_ids.add(operation_id)

            permission = operation.get("x-permission")
            if permission:
                api_permissions.add(permission)
                if permission not in registry:
                    errors.append(f"{method.upper()} {path} 引用了未注册权限码：{permission}")
            elif path not in PUBLIC_PATHS:
                errors.append(f"{method.upper()} {path} 是受保护路由但缺少 x-permission")

    menu_permissions = collect_menu_permissions(MENU_TREE)
    check_routes(collect_menu_paths(MENU_TREE), errors)
    check_page_scripts(errors)
    check_page_layouts(errors)
    check_component_keys(errors)
    check_dom_ids(errors)
    unused = registry - api_permissions - menu_permissions
    for code in sorted(unused):
        errors.append(f"权限码未被 API 或菜单使用：{code}")

    for code in sorted(menu_permissions - registry):
        errors.append(f"菜单引用了未注册权限码：{code}")

    if errors:
        print("契约检查失败：")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        "契约检查通过："
        f"{len(operation_ids)} 个操作，{len(api_permissions)} 个 API 权限，"
        f"{len(registry)} 个注册权限码，{len(PAGES)} 个页面。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
