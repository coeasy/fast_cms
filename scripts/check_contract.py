"""OpenAPI 契约与权限覆盖检查，供 CI / make contract-check 使用。

前端是后端直接渲染的 Jinja2 页面 + 原生 ES Module，
因此「路由 / 页面脚本」两边都要和后端注册的菜单对得上：
菜单里有的路径必须能打开页面，页面脚本必须存在，孤儿页面也要报出来。

另含「结构化契约 diff」（6-1）：把当前 openapi.json 与基线 openapi.baseline.json
对比，区分兼容/破坏性变更。破坏性变更（删端点、删字段、改 operationId、改权限、
删响应码、新增必填参数、字段变必填）默认失败，必须显式 `--allow-breaking` 才放行；
这样「重新导出 openapi.json 再提交」不再能静默合法化破坏性变更。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import string
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WEB = BACKEND / "app" / "web"
PAGES_JS = WEB / "static" / "js" / "pages"
sys.path.insert(0, str(BACKEND))

from app.bootstrap.menus import all_menu_tree  # noqa: E402
from app.bootstrap.permissions import all_permissions  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.errors import ERROR_CODE_STATUS  # noqa: E402
from app.core.messages import CONTEXT_MESSAGES, MESSAGES  # noqa: E402
from app.web.router import all_pages, templates  # noqa: E402


def pages() -> tuple[dict[str, Any], ...]:
    """存量页面 + 模块清单声明的页面。

    页面校验必须覆盖模块声明的页面：否则新模块只要在 module.py 里挂页面，
    模板/脚本/布局/#id 这几项就全都绕过了检查。
    """
    return all_pages()

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

# 错误码由**参数**决定、AST 看不到字面量的文件。
# 目前只有 kernel/db.py::commit(conflict_code=...)：唯一约束冲突的真实原因只有调用方
# 知道（主题编码重复 / 用户名重复 / 角色编码重复），码必须从外面传进来。
# 代价是门禁看不到码，所以该函数在运行时用 ERROR_CODE_STATUS 校验，写错码立刻 ValueError。
DYNAMIC_ERROR_CODE_CALLERS = {
    "app/kernel/db.py": "commit(conflict_code=...) 的码由调用方给，运行时校验",
}


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
    declared = {str(page["path"]) for page in pages()}
    for path in sorted(set(menu_paths) - declared):
        errors.append(f"菜单路径 {path} 没有对应的后端页面路由，点击会落到 404")
    for path in sorted(declared - set(menu_paths) - NON_MENU_ROUTES):
        errors.append(f"页面路由 {path} 没有菜单入口，属于孤儿页面")


def check_page_scripts(errors: list[str]) -> None:
    """每个页面都必须有对应的模板，且（登录/错误页除外）有同名页面脚本。"""
    for page in pages():
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
    for page in pages():
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
    for component_key in collect_component_keys(all_menu_tree()):
        if not (PAGES_JS / f"{component_key}.js").exists():
            errors.append(f"菜单组件未注册：static/js/pages/{component_key}.js")


# 页面脚本按 id 取节点，模板改结构时最容易漏掉；这里把两边对一遍。
ID_IN_JS = re.compile(r"""\$\(\s*['"]#([A-Za-z][\w-]*)['"]""")
QUERY_IN_JS = re.compile(r"""querySelector(?:All)?\(\s*['"]#([A-Za-z][\w-]*)['"]""")
# createCrudPage 的配置里 id 是以普通字符串传的（`id: '#dept-form'`），不经过 $(...)，
# 上面两条正则看不到。这里按**键名**精确匹配，避免把 `color: '#fff'` 这类色值当 id。
CONFIG_ID_IN_JS = re.compile(
    r"""(?:\bid|titleId|submitId|filters)\s*:\s*['"]#([A-Za-z][\w-]*)['"]"""
)
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
    for page in pages():
        try:
            html = render_page(
                str(page["template"]),
                page_title=page["title"],
                page_subtitle=page.get("subtitle", ""),
                page_path=page["path"],
                page_key=page["page"],
            )
        except Exception as exc:
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
        selectors = set(ID_IN_JS.findall(source)) | set(QUERY_IN_JS.findall(source)) | set(CONFIG_ID_IN_JS.findall(source))
        for node_id in sorted(selectors):
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


def check_response_schemas(spec: dict, errors: list[str]) -> None:
    """每个操作的 200 响应都必须声明 schema。

    空 schema 等于没有响应契约：前端生成不出类型，也挡不住响应结构漂移。
    这不是洁癖——``/security/ip-locate`` 曾把带 ``bytes`` 的内部对象塞进 ``data``，
    静态检查看不出来，一装好归属地库就 500。声明 ``Envelope[...]`` 之后，
    同类问题会在出站序列化前变成明确的校验错误。

    文件流端点（CSV / zip / 图片）用 ``text/csv`` / ``application/octet-stream``
    的 binary schema 声明，同样算有契约。
    """
    for (method, path), operation in _operations(spec).items():
        responses = operation.get("responses") or {}
        response = responses.get("200") or responses.get("201")
        if not isinstance(response, dict):
            errors.append(f"{method} {path} 缺少 200 响应定义")
            continue
        content = response.get("content") or {}
        if not content:
            errors.append(f"{method} {path} 的 200 响应没有 content，等于没有响应契约")
            continue
        if not any(item.get("schema") for item in content.values() if isinstance(item, dict)):
            errors.append(f"{method} {path} 的 200 响应缺少 schema，等于没有响应契约")


def _template_fields(template: str) -> set[str]:
    """取出文案模板里的占位符名。

    位置占位符（``{}`` / ``{0}``）直接报错：模板与实参分离的意义在于「按名对齐」，
    位置占位符一改顺序就静默错位，比散落硬编码更危险。
    """
    fields: set[str] = set()
    for _literal, field, _spec, _conversion in string.Formatter().parse(template):
        if field is None:
            continue
        fields.add(field)
    return fields


def check_messages(errors: list[str]) -> None:
    """错误文案表与错误码表必须一一对应（6-3）。

    三张表各管一件事：``ERROR_CODE_STATUS`` 管状态码、``MESSAGES`` 管默认文案、
    ``CONTEXT_MESSAGES`` 管「允许调用点自带文案」的例外。任何一边漏登记都会
    让 ``AppError`` 运行时退化成「文案就是错误码」或者干脆 500，所以在这里卡住。
    """
    missing = sorted(set(ERROR_CODE_STATUS) - set(MESSAGES))
    for code in missing:
        errors.append(f"MESSAGES 缺少错误码 {code} 的默认文案")
    for code in sorted(set(MESSAGES) - set(ERROR_CODE_STATUS)):
        errors.append(f"MESSAGES 登记了未定义错误码 {code}（不在 ERROR_CODE_STATUS）")
    for code in sorted(CONTEXT_MESSAGES - set(ERROR_CODE_STATUS)):
        errors.append(f"CONTEXT_MESSAGES 登记了未定义错误码 {code}")
    for code, template in sorted(MESSAGES.items()):
        if not template.strip():
            errors.append(f"MESSAGES[{code}] 文案为空")
        for field in sorted(_template_fields(template)):
            if field == "" or field.isdigit():
                errors.append(f"MESSAGES[{code}] 使用了位置占位符 {{{field}}}，必须改用具名占位符")


def check_error_code_status(errors: list[str]) -> None:
    """每个业务错误码有唯一状态码（P2-4），且文案来自单一来源（6-3）。

    用 AST 扫描所有 ``AppError(...)`` 调用点：
    - 代码必须登记在 ``core.errors.ERROR_CODE_STATUS``；
    - 调用处显式传入的 ``status_code`` 必须与契约一致，防止状态码漂移；
    - 未显式传状态码的调用点也要能解析（即代码已登记）；
    - 字面量文案只允许出现在 ``CONTEXT_MESSAGES`` 登记过的错误码上，
      其余必须交给 ``MESSAGES``——这样「新增文案散落硬编码」在门禁层就不可能发生；
    - 模板化文案（含 ``{name}``）必须给 ``params``，且实参名与占位符一致。
    调用方一旦新增/改错状态码，契约检查立即失败。

    例外见 ``DYNAMIC_ERROR_CODE_CALLERS``：极少数函数把错误码当参数收（目前只有
    ``kernel.db.commit(conflict_code=...)``），调用点拿不到字面量。这类位置必须在
    运行时自行校验码的合法性，否则门禁形同虚设。
    """
    app_dir = BACKEND / "app"
    for path in sorted(app_dir.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in DYNAMIC_ERROR_CODE_CALLERS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            errors.append(f"无法解析 {path.relative_to(BACKEND)}，语法错误")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "AppError"):
                continue
            args = node.args
            code = None
            if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                code = args[0].value
            status_node = None
            if len(args) >= 3:
                status_node = args[2]
            message_node = None
            if len(args) >= 2:
                message_node = args[1]
            params_node = None
            for kw in node.keywords:
                if kw.arg == "status_code":
                    status_node = kw.value
                elif kw.arg == "message":
                    message_node = kw.value
                elif kw.arg == "params":
                    params_node = kw.value
            if code is None:
                errors.append(
                    f"{path.relative_to(BACKEND)}:{node.lineno} AppError 使用了非常量错误码"
                )
                continue
            canonical = ERROR_CODE_STATUS.get(code)
            if canonical is None:
                errors.append(
                    f"{path.relative_to(BACKEND)}:{node.lineno} 错误码 {code} 未登记到 ERROR_CODE_STATUS"
                )
                continue
            where = f"{path.relative_to(BACKEND)}:{node.lineno}"
            if status_node is not None:
                status = (
                    status_node.value
                    if isinstance(status_node, ast.Constant) and isinstance(status_node.value, int)
                    else None
                )
                if status is None:
                    errors.append(f"{where} 错误码 {code} 使用了非常量状态码")
                elif status != canonical:
                    errors.append(f"{where} 错误码 {code} 状态码漂移：调用处 {status} != 契约 {canonical}")
            _check_message_usage(errors, where, code, message_node, params_node)


def _check_message_usage(
    errors: list[str],
    where: str,
    code: str,
    message_node: ast.AST | None,
    params_node: ast.AST | None,
) -> None:
    """校验单个调用点的文案写法（见 check_error_code_status 的说明）。"""
    template = MESSAGES.get(code, "")
    fields = _template_fields(template)
    is_literal = (
        message_node is not None
        and isinstance(message_node, ast.Constant)
        and isinstance(message_node.value, str)
    )
    if is_literal and code not in CONTEXT_MESSAGES:
        errors.append(
            f"{where} 错误码 {code} 在调用点写死了文案，请移到 core/messages.py 的 MESSAGES；"
            "确实需要按场景区分措辞时，把错误码登记到 CONTEXT_MESSAGES"
        )
    if message_node is None and fields and params_node is None:
        # 没给 message：文案完全由 MESSAGES 提供，模板化条目必须补 params
        errors.append(f"{where} 错误码 {code} 的文案含占位符 {sorted(fields)}，必须传 params")
    if params_node is None:
        return
    if message_node is not None:
        errors.append(f"{where} 错误码 {code} 同时传了 message 与 params，params 不会生效")
        return
    if not fields:
        errors.append(f"{where} 错误码 {code} 传了 params，但 MESSAGES 里的文案没有占位符")
        return
    if not isinstance(params_node, ast.Dict):
        return
    keys = {key.value for key in params_node.keys if isinstance(key, ast.Constant)}
    if len(keys) != len(params_node.keys):
        return  # 有展开（**kwargs）的写法不做静态校验
    missing = sorted(fields - keys)
    extra = sorted(keys - fields)
    if missing:
        errors.append(f"{where} 错误码 {code} 的 params 缺少占位符实参 {missing}")
    if extra:
        errors.append(f"{where} 错误码 {code} 的 params 多出模板里没有的实参 {extra}")


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _operations(spec: dict) -> dict[tuple[str, str], dict]:
    """把 OpenAPI 摊平成 {(METHOD, path): operation}。"""
    ops: dict[tuple[str, str], dict] = {}
    for path, methods in (spec.get("paths") or {}).items():
        for method, operation in methods.items():
            if method.lower() in _HTTP_METHODS and isinstance(operation, dict):
                ops[(method.upper(), path)] = operation
    return ops


def _params(operation: dict) -> dict[tuple[str, str], dict]:
    return {
        (str(p.get("name")), str(p.get("in"))): p
        for p in (operation.get("parameters") or [])
        if isinstance(p, dict)
    }


def diff_specs(baseline: dict, current: dict) -> tuple[list[str], list[str]]:
    """对比两份 OpenAPI，返回 (兼容变更, 破坏性变更)。

    破坏性：删端点/改 operationId/改权限码/删参数/新增必填参数/删响应码/
    删 schema/删字段/字段变必填。其余（新增端点、可选参数、响应码、字段）视为兼容。
    """
    compatible: list[str] = []
    breaking: list[str] = []
    base_ops = _operations(baseline)
    cur_ops = _operations(current)

    for key in sorted(set(base_ops) - set(cur_ops)):
        breaking.append(f"移除端点 {key[0]} {key[1]}")
    for key in sorted(set(cur_ops) - set(base_ops)):
        compatible.append(f"新增端点 {key[0]} {key[1]}")

    for key in sorted(set(base_ops) & set(cur_ops)):
        base, cur = base_ops[key], cur_ops[key]
        method, path = key
        if base.get("operationId") != cur.get("operationId"):
            breaking.append(f"{method} {path} operationId 变更：{base.get('operationId')} -> {cur.get('operationId')}")
        if base.get("x-permission") != cur.get("x-permission"):
            breaking.append(f"{method} {path} 权限码变更：{base.get('x-permission')} -> {cur.get('x-permission')}")

        base_params, cur_params = _params(base), _params(cur)
        for pk in sorted(set(base_params) - set(cur_params)):
            breaking.append(f"{method} {path} 移除参数 {pk[1]}:{pk[0]}")
        for pk in sorted(set(cur_params) - set(base_params)):
            if cur_params[pk].get("required"):
                breaking.append(f"{method} {path} 新增必填参数 {pk[1]}:{pk[0]}")
            else:
                compatible.append(f"{method} {path} 新增可选参数 {pk[1]}:{pk[0]}")

        base_resp = set(base.get("responses") or {})
        cur_resp = set(cur.get("responses") or {})
        for code in sorted(base_resp - cur_resp):
            breaking.append(f"{method} {path} 移除响应码 {code}")
        for code in sorted(cur_resp - base_resp):
            compatible.append(f"{method} {path} 新增响应码 {code}")

    base_schemas = (baseline.get("components") or {}).get("schemas") or {}
    cur_schemas = (current.get("components") or {}).get("schemas") or {}
    for name in sorted(set(base_schemas) - set(cur_schemas)):
        breaking.append(f"移除 schema {name}")
    for name in sorted(set(cur_schemas) - set(base_schemas)):
        compatible.append(f"新增 schema {name}")
    for name in sorted(set(base_schemas) & set(cur_schemas)):
        base_props = set(base_schemas[name].get("properties") or {})
        cur_props = set(cur_schemas[name].get("properties") or {})
        for prop in sorted(base_props - cur_props):
            breaking.append(f"schema {name} 移除字段 {prop}")
        for prop in sorted(cur_props - base_props):
            compatible.append(f"schema {name} 新增字段 {prop}")
        base_req = set(base_schemas[name].get("required") or [])
        cur_req = set(cur_schemas[name].get("required") or [])
        for prop in sorted(cur_req - base_req):
            if prop in base_props:
                breaking.append(f"schema {name} 字段 {prop} 变为必填")

    return compatible, breaking


def check_contract_diff(
    current: dict, baseline_path: Path, allow_breaking: bool, update_baseline: bool, errors: list[str]
) -> None:
    """结构化 diff：破坏性变更默认失败，除非显式 --allow-breaking。"""
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已初始化契约基线：{baseline_path.name}")
        return
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    compatible, breaking = diff_specs(baseline, current)

    if update_baseline:
        if breaking and not allow_breaking:
            errors.append(
                f"契约基线更新被拒绝：存在 {len(breaking)} 项破坏性变更，"
                "如确认要接受，请同时传 --allow-breaking"
            )
            return
        baseline_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"契约基线已更新（兼容 {len(compatible)} 项，破坏 {len(breaking)} 项）")
        return

    if breaking:
        if allow_breaking:
            print(f"已允许 {len(breaking)} 项破坏性契约变更：")
            for item in breaking:
                print(f"  - {item}")
        else:
            for item in breaking:
                errors.append(f"破坏性契约变更：{item}（如确认接受，运行 check_contract.py --allow-breaking）")
    if compatible:
        print(f"契约兼容变更 {len(compatible)} 项（不破坏旧客户端）。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI 契约与权限覆盖检查")
    parser.add_argument("--allow-breaking", action="store_true", help="允许破坏性契约变更（需人工确认）")
    parser.add_argument("--update-baseline", action="store_true", help="把当前 openapi.json 写为新的契约基线")
    parser.add_argument("--baseline", default=str(ROOT / "openapi.baseline.json"), help="契约基线文件路径")
    args = parser.parse_args(argv)

    spec = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    registry = {item["code"] for item in all_permissions()}
    errors: list[str] = []
    operation_ids: set[str] = set()
    api_permissions: set[str] = set()

    check_contract_diff(
        spec, Path(args.baseline), args.allow_breaking, args.update_baseline, errors
    )

    check_prefix(list(spec.get("paths", {})), errors)
    check_response_schemas(spec, errors)
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

    menu_permissions = collect_menu_permissions(all_menu_tree())
    check_routes(collect_menu_paths(all_menu_tree()), errors)
    check_page_scripts(errors)
    check_page_layouts(errors)
    check_component_keys(errors)
    check_dom_ids(errors)
    check_messages(errors)
    check_error_code_status(errors)
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
        f"{len(registry)} 个注册权限码，{len(pages())} 个页面。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
