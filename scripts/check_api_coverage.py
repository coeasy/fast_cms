"""接口连通性体检：核对 openapi.json 里的每个端点是否真的被前端调用。

用法：
    python scripts/check_api_coverage.py            # 只报告
    python scripts/check_api_coverage.py --strict   # 有孤儿则退出码 1（CI 闸门）

判定口径：
- 前端是后端直接渲染的 Jinja2 页面 + 原生 ES Module，调用点分布在
  backend/app/web/static/js/pages/**.js（后台页面脚本）与 static/js/shell/*.js
  （外壳：后台登录守卫与公告、登录页、前台首页）；
- HTTP 方法必须一致，否则 PUT /x/{id} 会把 GET /x/{id} 误判成已连通；
  方法由参数决定的调用（download）按通配处理；
- 模板串里的 ${expr} 对应 openapi 的 {param}；
- REST_DETAIL_ENDPOINTS 里的单条详情 GET 允许没有前端调用方，理由见常量注释。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_PREFIX = "/api/v1"
JS_DIR = ROOT / "backend/app/web/static/js"
# 只有这些位置会出现真实调用：页面脚本 + 三套外壳。core.js 是请求封装本身，icons.js 无请求。
CORPUS_FILES = (
    JS_DIR / "app.js",
    JS_DIR / "shell" / "admin.js",
    JS_DIR / "shell" / "auth.js",
    JS_DIR / "shell" / "portal.js",
)
CORPUS_DIRS = (JS_DIR / "pages",)

# 运维探活端点由部署脚本/负载均衡调用，前端不应该访问
OPS_ENDPOINTS = {"/healthz", "/readyz"}
# 不由 api 包装、直接在请求层拼接的端点
MANUAL_CALLS = {("POST", "/auth/refresh")}

WRAPPERS = {
    "get": "GET",
    "getBlob": "GET",
    # download() 的方法由第二个 config 参数决定，先记为通配
    "download": "*",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "del": "DELETE",
    # DELETE 带请求体（批量删除）单独一个入口，否则会被漏检成孤儿端点
    "delBody": "DELETE",
    "upload": "POST",
}

# 详情类 GET 端点：列表接口已返回整行数据，页面不再回查单条；
# 它们是脚手架对外提供的完整 REST 能力，由后端集成测试覆盖鉴权与数据范围。
REST_DETAIL_ENDPOINTS = {
    ("GET", "/content/notices/{notice_id}"),
    ("GET", "/notify/channels/{channel_id}"),
    ("GET", "/schedule/jobs/{job_id}"),
    ("GET", "/schedule/templates/{template_id}"),
    ("GET", "/system/dict-types/{type_id}"),
    ("GET", "/system/roles/{role_id}"),
    ("GET", "/system/themes/{theme_id}"),
    ("GET", "/system/users/{user_id}"),
    ("GET", "/tool/files/{file_id}"),
}

STRING_BODY = r"(?:[^`'\\]|\\.|\$\{[^}]*\})*"
QUOTE = r"(?P<quote>[`'])"
CLOSE = r"(?P=quote)"
CALL_RE = re.compile(
    rf"\b(?:api\s*\.\s*)?(?P<verb>get|getBlob|download|post|put|patch|del|delBody|upload)\s*\(\s*{QUOTE}(?P<path>{STRING_BODY}){CLOSE}"
)
API_PATH_RE = re.compile(rf"\bapiPath\(\s*{QUOTE}(?P<path>{STRING_BODY}){CLOSE}")


def source_files() -> list[Path]:
    files = [path for path in CORPUS_FILES if path.exists()]
    for directory in CORPUS_DIRS:
        if not directory.exists():
            continue
        files.extend(sorted(path for path in directory.rglob("*.js") if path.is_file()))
    return files


def path_to_regex(call: str) -> re.Pattern[str] | None:
    """把前端调用路径编译成能匹配 openapi 模板路径的正则。"""
    path = call.split("?")[0].split("#")[0]
    segs = re.split(r"(\$\{[^}]*\})", path)
    body = "".join("[^/]+" if seg.startswith("${") else re.escape(seg) for seg in segs)
    if len(body) < 2:
        return None
    return re.compile("^" + body.rstrip("/") + "/?$")


def extract_calls(text: str) -> set[tuple[str, str]]:
    """返回 {(HTTP 方法, 去掉 API 前缀的路径)}；方法由参数决定的调用记为通配 *。"""
    calls: set[tuple[str, str]] = set()
    for match in CALL_RE.finditer(text):
        calls.add((WRAPPERS[match.group("verb")], match.group("path")))
    for match in API_PATH_RE.finditer(text):
        calls.add(("*", match.group("path")))
    normalized: set[tuple[str, str]] = set()
    for method, raw in calls:
        if not raw.startswith("/"):
            continue
        suffix = raw[len(API_PREFIX):] if raw.startswith(API_PREFIX) else raw
        normalized.add((method, suffix))
    return normalized


def spec_endpoints() -> dict[tuple[str, str], str]:
    spec = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    endpoints: dict[tuple[str, str], str] = {}
    for path, operations in spec["paths"].items():
        suffix = path[len(API_PREFIX):] if path.startswith(API_PREFIX) else path
        for method, operation in operations.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                endpoints[(method.upper(), suffix)] = operation.get("summary", "")
    return endpoints


def check_whitelist(endpoints: dict[tuple[str, str], str]) -> list[str]:
    """白名单不是免检：条目必须仍然存在，且后端测试要真的请求过这个路径。"""
    problems: list[str] = []
    tests_dir = ROOT / "backend/tests"
    corpus = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in sorted(tests_dir.rglob("*.py"))
    )
    for method, suffix in sorted(REST_DETAIL_ENDPOINTS):
        if (method, suffix) not in endpoints:
            problems.append(f"白名单条目已失效：{method} {suffix}")
            continue
        concrete = re.sub(r"\{[^}]*\}.*$", "", suffix).rstrip("/") + "/"
        if concrete not in corpus:
            problems.append(f"白名单端点缺少后端测试：{method} {suffix}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="检查后端端点与前端调用的连通性")
    parser.add_argument("--strict", action="store_true", help="发现孤儿端点时返回非零退出码")
    args = parser.parse_args()

    endpoints = spec_endpoints()
    files = source_files()
    if not files:
        print(f"未找到前端语料：{JS_DIR}")
        print("该脚本只对源码仓库有效，请确认 backend/app/web/static/js 存在")
        return 1
    calls: set[tuple[str, str]] = set()
    for path in files:
        calls |= extract_calls(path.read_text(encoding="utf-8"))

    regexes: list[tuple[str, str, re.Pattern[str]]] = []
    for method, suffix in sorted(calls):
        compiled = path_to_regex(suffix)
        if compiled:
            regexes.append((method, suffix, compiled))

    orphan_endpoints: list[tuple[str, str]] = []
    whitelisted = 0
    for (method, suffix) in sorted(endpoints):
        if suffix in OPS_ENDPOINTS or (method, suffix) in MANUAL_CALLS:
            continue
        if (method, suffix) in REST_DETAIL_ENDPOINTS:
            whitelisted += 1
            continue
        covered = any(
            (call_method == "*" or call_method == method) and compiled.search(suffix)
            for call_method, _, compiled in regexes
        )
        if not covered:
            orphan_endpoints.append((method, suffix))

    dead_calls: list[str] = []
    for call_method, suffix, compiled in regexes:
        matched = any(
            (call_method == "*" or call_method == spec_method) and compiled.search(spec_suffix)
            for spec_method, spec_suffix in endpoints
        )
        if not matched:
            dead_calls.append(f"{call_method} {suffix}")
    dead_calls = sorted(set(dead_calls))

    whitelist_problems = check_whitelist(endpoints)

    print(f"openapi 端点：{len(endpoints)}，前端调用点：{len(regexes)}，语料文件：{len(files)}")
    print(f"未被前端调用的端点：{len(orphan_endpoints)}（另有 {whitelisted} 个单条详情 GET 走白名单）")
    for method, suffix in orphan_endpoints:
        print(f"  ORPHAN_ENDPOINT {method} {suffix}  [{endpoints[(method, suffix)]}]")
    print(f"前端调用但 openapi 中不存在：{len(dead_calls)}")
    for item in dead_calls:
        print(f"  DEAD_CALL {item}")
    print(f"白名单问题：{len(whitelist_problems)}")
    for item in whitelist_problems:
        print(f"  WHITELIST {item}")

    if args.strict and (orphan_endpoints or dead_calls or whitelist_problems):
        print("连通性检查未通过")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
