"""P1-2：迁移元数据一致性 lint。

保证：
- 每个迁移 docstring 的 ``Revises`` 与 ``down_revision`` 一致（review 不被误导）；
- 迁移链线性、无分叉、无悬挂（每个非根 revision 恰好被一个下游引用）；
- ``alembic history`` 可读、能解析出完整链。
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic.script import ScriptDirectory
from alembic.script.base import Script

from app.core.config import BASE_DIR
from app.db.migrate import alembic_config

MIGRATIONS_DIR = BASE_DIR / "migrations" / "versions"


def _revision_id(src: str, name: str) -> str | None:
    match = re.search(rf"^{name}.*?[\"']([A-Za-z0-9_]+)[\"']", src, re.M)
    return match.group(1) if match else None


def _docstring_revises(src: str) -> str | None:
    # Revises: 后只允许同行空白，不能吞到下一行的 Create Date
    match = re.search(r"Revises:[ \t]*([A-Za-z0-9_]*)", src)
    return match.group(1) or None if match else None


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.py"))


def test_every_migration_docstring_matches_down_revision() -> None:
    """docstring 的 Revises 是给 review 读的，必须与真正的 down_revision 一致。"""
    for path in _migration_files():
        if path.name == "__init__.py":
            continue
        src = path.read_text(encoding="utf-8")
        down = _revision_id(src, "down_revision")
        doc = _docstring_revises(src)
        # 根迁移 down_revision=None，docstring 写作 Revises:（空）
        expected = down or ""
        assert (doc or "") == expected, f"{path.name}: docstring Revises={doc!r} 但 down_revision={down!r}"


def test_migration_chain_is_linear_and_complete() -> None:
    """revision 链线性：每个 revision 恰好一个下游；根只有一个；无悬挂引用。"""
    files = [p for p in _migration_files() if p.name != "__init__.py"]
    revisions: dict[str, str | None] = {}
    for path in files:
        src = path.read_text(encoding="utf-8")
        rev = _revision_id(src, "revision")
        down = _revision_id(src, "down_revision")
        assert rev is not None, f"{path.name}: 缺少 revision 定义"
        revisions[rev] = down

    referenced: list[str | None] = list(revisions.values())
    roots = [rev for rev, down in revisions.items() if down is None]
    assert len(roots) == 1, f"应只有 1 个根迁移，实际 {roots}"
    # 每个非根 revision 恰被一个下游引用（线性无分叉）
    for rev, down in revisions.items():
        if down is not None:
            assert down in revisions, f"{down} 被 {rev} 引用但不存在"
            assert referenced.count(down) == 1, f"{down} 被多个迁移引用（存在分叉）"
    assert all(down in revisions for down in referenced if down is not None)


def test_alembic_history_readable() -> None:
    """`alembic history` 能解析；脚本目录 head 与链尾一致。"""
    script = ScriptDirectory.from_config(alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, f"应只有 1 个 head，实际 {heads}"
    # 遍历整条链确认无断点
    cursor: Script | None = script.get_revision(heads[0])
    count = 0
    while cursor is not None:
        count += 1
        cursor = script.get_revision(cursor.down_revision) if cursor.down_revision else None
    assert count == len(_migration_files()), f"链长度 {count} 与迁移文件数 {len(_migration_files())} 不一致"
