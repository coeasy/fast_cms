from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.codegen.generator import CodeGenerator
from app.core.errors import AppError
from app.web.router import WEB_DIR

# 页面脚本里用 $('#xxx') / querySelector('#xxx') 取的 id
_SCRIPT_ID_REF = re.compile(r"""\$\('#([A-Za-z0-9_-]+)'\)|querySelector\('#([A-Za-z0-9_-]+)'\)""")
_HTML_ID_DEF = re.compile(r'id="([^"]+)"')


def _run(coroutine):
    return asyncio.run(coroutine)


async def _make_generator(directory: Path) -> CodeGenerator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(directory / 'codegen.db').as_posix()}")
    async with engine.begin() as connection:  # noqa: SIM117
        await connection.execute(
            text(
                "CREATE TABLE demo_article ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "title VARCHAR(120) NOT NULL, "
                "views INTEGER DEFAULT 0, "
                "price NUMERIC(10, 2), "
                "created_at DATETIME)"
            )
        )
        await connection.execute(text("CREATE TABLE sys_style (id INTEGER PRIMARY KEY, name VARCHAR(20))"))
        await connection.execute(
            text(
                "CREATE TABLE demo_link ("
                "article_id INTEGER, tag_id INTEGER, PRIMARY KEY (article_id, tag_id))"
            )
        )
    return CodeGenerator(engine)


def test_generate_crud_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        generator = _run(_make_generator(Path(tmp)))
        try:
            files = _run(
                generator.generate(table="demo_article", module="demo", resource="article", title="文章")
            )
        finally:
            _run(generator.engine.dispose())
        assert set(files) == {
            "model.py",
            "schemas.py",
            "service.py",
            "router.py",
            "frontend/page.html",
            "frontend/index.js",
            "README.md",
            "manifest.json",
        }
        assert "class Article(Base)" in files["model.py"]
        assert '__tablename__ = "demo_article"' in files["model.py"]
        assert "demo:article:list" in files["router.py"]
        assert "文章" in files["README.md"]
        manifest = json.loads(files["manifest.json"])
        assert set(manifest["files"]) == set(files) - {"manifest.json"}
        assert manifest["files"]["router.py"] == hashlib.sha256(files["router.py"].encode()).hexdigest()


def test_generator_refuses_system_and_composite_tables() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        generator = _run(_make_generator(Path(tmp)))
        try:
            with pytest.raises(AppError) as system_error:
                _run(generator.table_spec("sys_style"))
            assert system_error.value.code == "CODEGEN_TABLE_SYSTEM"
            with pytest.raises(AppError) as composite_error:
                _run(generator.table_spec("demo_link"))
            assert composite_error.value.code == "CODEGEN_COMPOSITE_KEY"
        finally:
            _run(generator.engine.dispose())


def test_generated_python_compiles_and_write_is_guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        generator = _run(_make_generator(Path(tmp)))
        try:
            files = _run(
                generator.generate(table="demo_article", module="demo", resource="article", title="文章")
            )
        finally:
            _run(generator.engine.dispose())

    for path, content in files.items():
        if path.endswith(".py"):
            compile(content, path, "exec")

    import app.codegen.generator as generator_module

    monkeypatch.setattr(generator_module, "GENERATED_ROOT", tmp_path)
    target = tmp_path / "demo" / "article"
    written = CodeGenerator.write_files(files, target)
    assert len(written) == len(files)
    assert (target / "manifest.json").exists()

    with pytest.raises(AppError) as duplicate:
        CodeGenerator.write_files(files, target)
    assert duplicate.value.code == "CODEGEN_TARGET_EXISTS"

    with pytest.raises(AppError) as invalid:
        CodeGenerator.write_files(files, tmp_path.parent / "outside")
    assert invalid.value.code == "CODEGEN_PATH_INVALID"


def test_generator_rejects_invalid_identifier() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        generator = _run(_make_generator(Path(tmp)))
        try:
            with pytest.raises(AppError) as excinfo:
                _run(generator.table_spec("demo_article;drop"))
            assert excinfo.value.code == "CODEGEN_TABLE_INVALID"
        finally:
            _run(generator.engine.dispose())


def _generate_sample() -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        generator = _run(_make_generator(Path(tmp)))
        try:
            return _run(generator.generate(table="demo_article", module="demo", resource="article", title="文章"))
        finally:
            _run(generator.engine.dispose())


def test_generated_frontend_page_renders_with_component_library() -> None:
    """生成的 page.html 必须真的套上组件库与后台外壳渲染出完整 HTML。

    代码生成产物直接落进 templates/，宏名或参数写错就是线上 500。
    这里用真实的 Jinja 环境渲染一遍，顺便把页面脚本里的 #id 选择器
    和生成的 HTML 对一遍，避免「模板改了、脚本没改」的静默错位。
    """
    files = _generate_sample()

    env = Environment(loader=FileSystemLoader(str(WEB_DIR / "templates")), autoescape=True)
    html = env.from_string(files["frontend/page.html"]).render(
        base="/admin/",
        admin_base="/admin",
        portal_base="/front",
        api_prefix="/api/v1",
        app_name="FastCMS",
        app_version="0.0.0",
        page_title="文章管理",
        page_subtitle="演示模块",
        page_path="/demo/article",
        page_key="demo/article",
        code="",
        message="",
    )

    # 分层样式：base.css 来自 base.html，admin.css 来自 layouts/admin.html
    assert 'href="/admin/static/css/base.css"' in html
    assert 'href="/admin/static/css/admin.css"' in html
    assert 'class="portal-nav"' not in html

    # 组件宏都正常展开
    assert 'class="page-head"' in html
    assert 'class="filters" id="filters"' in html
    assert '<table class="data">' in html
    assert 'class="pager"' in html
    assert 'id="article-modal"' in html
    assert 'id="article-modal-title"' in html
    assert 'id="article-submit"' in html
    assert 'name="title"' in html

    # 生成脚本引用的每个 #id 都必须在生成页面里存在
    script_ids = {a or b for a, b in _SCRIPT_ID_REF.findall(files["frontend/index.js"])}
    assert script_ids, "生成脚本里没有解析到任何 id 选择器"
    assert script_ids <= set(_HTML_ID_DEF.findall(html)), script_ids - set(_HTML_ID_DEF.findall(html))
