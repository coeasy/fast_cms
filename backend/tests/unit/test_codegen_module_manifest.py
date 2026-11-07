"""代码生成器产物与模块清单机制的闭环校验。

背景：模块改为「清单 + 自动发现」后，生成的代码如果没有 `module.py`，
复制进 `app/modules/` 也不会被 `discover_modules()` 发现——路由不注册、
权限码不入库、菜单与页面不出现，且全部是静默故障（404 / 菜单缺失）。
本文件把这条链路钉死：

1. 生成产物必须包含 `module.py`，且被 `manifest.json` 收录；
2. 清单内容可执行、`ModuleSpec.validate()` 通过，权限码与路由声明一致；
3. 清单声明的 pages / menus 会被 `web.router.all_pages()` 与
   `bootstrap.menus.all_menu_tree()` 真实合并（而不是声明了没人读）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.bootstrap.menus import MENU_TREE, all_menu_tree
from app.codegen.generator import GENERATED_MODULE_ORDER, CodeGenerator
from app.core.module import ModuleSpec
from app.web.router import PAGES, all_pages

MODULE = "demo_gen"
RESOURCE = "article"


def _run(coroutine):
    return asyncio.run(coroutine)


async def _generate(directory: Path, title: str = "文章") -> dict[str, str]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(directory / 'gen.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE demo_article ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "title VARCHAR(120) NOT NULL, "
                "views INTEGER DEFAULT 0)"
            )
        )
    generator = CodeGenerator(engine)
    try:
        return await generator.generate(
            table="demo_article", module=MODULE, resource=RESOURCE, title=title
        )
    finally:
        await engine.dispose()


def _load_generated_manifest(source: str) -> ModuleSpec:
    """在「假包」环境里执行生成的 module.py，拿到真实 ModuleSpec。

    生成的清单第一行就 import 自己的 router，直接 exec 会触发整个应用导入；
    这里用 sys.modules 注入一个只有空 APIRouter 的桩包，隔离被测内容。
    """
    package = f"app.modules.{MODULE}"
    stub_pkg = types.ModuleType(package)
    stub_pkg.__path__ = []  # type: ignore[attr-defined]
    stub_router = types.ModuleType(f"{package}.router")
    stub_router.router = APIRouter()  # type: ignore[attr-defined]
    saved = {name: sys.modules.get(name) for name in (package, f"{package}.router")}
    sys.modules[package] = stub_pkg
    sys.modules[f"{package}.router"] = stub_router
    try:
        namespace: dict[str, object] = {}
        exec(compile(source, "module.py", "exec"), namespace)
        spec = namespace["MODULE"]
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    assert isinstance(spec, ModuleSpec)
    return spec


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return _run(_generate(tmp_path_factory.mktemp("codegen")))


def test_generated_files_include_module_manifest(generated: dict[str, str]) -> None:
    assert "module.py" in generated
    manifest = json.loads(generated["manifest.json"])
    assert "module.py" in manifest["files"], "manifest.json 必须收录 module.py"


def test_generated_manifest_is_valid_module_spec(generated: dict[str, str]) -> None:
    spec = _load_generated_manifest(generated["module.py"])
    spec.validate()
    assert spec.name == MODULE
    assert spec.order == GENERATED_MODULE_ORDER
    assert spec.description, "清单必须写 description，否则模块列表里看不出用途"
    assert len(spec.routers) == 1


def test_generated_permissions_cover_router_declarations(generated: dict[str, str]) -> None:
    """清单里声明的权限码必须与 router.py 实际使用的 permission 完全一致。

    少一条 → 该接口 403；多一条 → 权限清单里出现永远用不上的码。
    """
    spec = _load_generated_manifest(generated["module.py"])
    declared = {item["code"] for item in spec.permissions}
    router_source = generated["router.py"]
    used = {
        token.split('"')[0]
        for token in router_source.split('permission="')[1:]
    }
    assert used == declared, f"清单与路由权限码不一致：清单多 {declared - used}，路由多 {used - declared}"
    for item in spec.permissions:
        assert item["module"] == MODULE
        assert item["name"].startswith(("查看", "创建", "更新", "删除"))


def test_generated_page_and_menu_point_to_same_path(generated: dict[str, str]) -> None:
    spec = _load_generated_manifest(generated["module.py"])
    assert len(spec.pages) == 1
    assert len(spec.menus) == 1
    page = spec.pages[0]
    menu = spec.menus[0]
    assert page["path"] == menu["path"] == f"/{MODULE}/{RESOURCE}"
    assert page["page"] == menu["component_key"] == f"{MODULE}/{RESOURCE}"
    assert page["template"] == f"pages/{MODULE}/{RESOURCE}.html"
    assert menu["permission_code"] in {item["code"] for item in spec.permissions}


def test_all_pages_merges_module_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单声明的页面必须被 all_pages() 真正合并，而不是躺在 ModuleSpec 里。"""
    extra = {
        "path": "/demo_gen/article",
        "template": "pages/demo_gen/article.html",
        "title": "文章",
        "page": "demo_gen/article",
    }
    monkeypatch.setattr("app.core.module.module_pages", lambda: [extra])
    merged = all_pages()
    assert extra["path"] in {page["path"] for page in merged}
    # 存量页面必须原样保留
    assert {page["path"] for page in PAGES} <= {page["path"] for page in merged}


def test_all_pages_keeps_baseline_on_path_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """模块声明了与内置页面相同的路径时，内置页面优先，不能被悄悄覆盖。"""
    hijack = {"path": "/dashboard", "template": "pages/evil.html", "title": "劫持", "page": "evil"}
    monkeypatch.setattr("app.core.module.module_pages", lambda: [hijack])
    merged = all_pages()
    dashboard = [page for page in merged if page["path"] == "/dashboard"]
    assert len(dashboard) == 1
    assert dashboard[0]["template"] == "pages/dashboard.html"


def test_all_menu_tree_merges_module_menus(monkeypatch: pytest.MonkeyPatch) -> None:
    extra = {
        "name": "文章",
        "path": "/demo_gen/article",
        "component_key": "demo_gen/article",
        "permission_code": "demo_gen:article:list",
        "sort": 60,
    }
    monkeypatch.setattr("app.core.module.module_menus", lambda: [extra])
    merged = all_menu_tree()
    assert extra["path"] in {node["path"] for node in merged}
    assert {node["path"] for node in MENU_TREE} <= {node["path"] for node in merged}


def test_all_menu_tree_dedupes_baseline_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    duplicate = {"name": "重复", "path": "/dashboard", "component_key": "dashboard", "sort": 1}
    monkeypatch.setattr("app.core.module.module_menus", lambda: [duplicate])
    merged = all_menu_tree()
    assert len([node for node in merged if node["path"] == "/dashboard"]) == 1


# ---------------------------------------------------------------------------
# 标题注入：标题会被原样插进 Python 字面量、docstring 与前端 JS 字符串
# ---------------------------------------------------------------------------

# 每一个都能让「直接插值」的产物变成 SyntaxError 或破坏字符串边界
NASTY_TITLES = [
    '他说"你好"',
    "O'Reilly 手册",
    "反斜杠\\路径",
    "三引号\"\"\"结束",
    "换行\n标题",
    "制表\t符",
    "   ",
]

# 产物里出现这些字符就会破坏某一种字面量：Python 用双引号、JS 用单引号
QUOTE_CHARS = ('"', "'", "`", "\\")


@pytest.mark.parametrize("raw", NASTY_TITLES)
def test_safe_title_removes_string_breakers(raw: str) -> None:
    from app.codegen.generator import safe_title

    cleaned = safe_title(raw)
    assert cleaned, "空标题要有兜底名，否则生成 description=\"\""
    for char in (*QUOTE_CHARS, "\n", "\t"):
        assert char not in cleaned


def test_safe_title_keeps_readable_text() -> None:
    from app.codegen.generator import safe_title

    assert safe_title("文章 管理（V2）") == "文章 管理（V2）"
    assert safe_title("a  b") == "a b"


@pytest.mark.parametrize("raw", NASTY_TITLES)
def test_generated_python_compiles_with_any_title(tmp_path: Path, raw: str) -> None:
    """标题里的引号曾让 module.py 变成 SyntaxError——用户复制进 app/modules 就崩。"""
    files = _run(_generate(tmp_path, title=raw))
    python_files = {name: body for name, body in files.items() if name.endswith(".py")}
    assert python_files
    for name, body in python_files.items():
        compile(body, name, "exec")  # 抛 SyntaxError 即失败


@pytest.mark.parametrize("raw", NASTY_TITLES)
def test_generated_js_has_no_broken_string(tmp_path: Path, raw: str) -> None:
    """前端模板把标题放进单引号字符串：标题带 `'` 会把 .js 打成语法错误。

    把 `"` 换成 `'` 只能把问题从 .py 挪到 .js，所以两种引号都必须去掉。
    """
    files = _run(_generate(tmp_path, title=raw))
    js = files["frontend/index.js"]
    assert "新建" in js
    # 标题所在行的引号必须成对：数一遍单引号是否为偶数即可暴露被标题截断的情况
    for line in js.splitlines():
        if "新建" in line:
            assert line.count("'") % 2 == 0, f"标题破坏了 JS 字符串：{line}"


def test_generated_title_survives_sanitizing(tmp_path: Path) -> None:
    files = _run(_generate(tmp_path, title='他说"你好"'))
    spec = _load_generated_manifest(files["module.py"])
    assert "你好" in spec.description
    for char in QUOTE_CHARS:
        assert char not in spec.description
