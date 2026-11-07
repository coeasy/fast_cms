"""B-11 / Phase 12-2：种子版本内容指纹。"""

from __future__ import annotations

import pytest

from app.bootstrap.seed import SEED_VERSION, _seed_fingerprint


def test_fingerprint_is_stable() -> None:
    """同一份声明两次计算结果一致（进程内确定性，跨 worker 也必须一致）。"""
    assert _seed_fingerprint() == _seed_fingerprint()
    assert _seed_fingerprint() == SEED_VERSION


def test_fingerprint_changes_with_declaration() -> None:
    """改任何一条内置声明（哪怕不递增任何版本号）指纹都会变。"""
    from app.bootstrap import seed as seed_mod
    from app.bootstrap.menus import all_menu_tree

    original = all_menu_tree()
    try:
        # 深拷贝一份并改名，替换模块内引用
        import copy

        tree = copy.deepcopy(original)
        tree[0]["name"] = "改名后的工作台"
        seed_mod.all_menu_tree = lambda: tree  # type: ignore[assignment]
        assert _seed_fingerprint() != SEED_VERSION
    finally:
        from app.bootstrap.menus import all_menu_tree as real_tree

        seed_mod.all_menu_tree = real_tree  # type: ignore[assignment]


def test_fingerprint_format() -> None:
    """带 fp- 前缀，长度受控，可以直接放进 bootstrap 表的 version 字段。"""
    assert SEED_VERSION.startswith("fp-")
    assert len(SEED_VERSION) == 19  # fp- + 16 位 hex


def test_fingerprint_covers_configs_depts_and_role_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """升级系统参数/默认部门/内置角色授权必须触发重播（B-11 盲区回归护栏）。"""
    from app.bootstrap import seed as seed_mod

    original_configs = seed_mod.SYSTEM_CONFIGS
    original_depts = seed_mod.DEFAULT_DEPTS
    try:
        seed_mod.SYSTEM_CONFIGS = [
            {"key": "x", "name": "x", "value": "y", "group": "basic", "is_public": True}
        ]
        assert _seed_fingerprint() != SEED_VERSION
        seed_mod.SYSTEM_CONFIGS = original_configs

        seed_mod.DEFAULT_DEPTS = [{"name": "x", "leader": "x", "sort": 1}]
        assert _seed_fingerprint() != SEED_VERSION
        seed_mod.DEFAULT_DEPTS = original_depts

        monkeypatch.setattr(seed_mod, "READ_ONLY_PERMISSIONS", frozenset({"system:user:list"}))
        assert _seed_fingerprint() != SEED_VERSION
    finally:
        seed_mod.SYSTEM_CONFIGS = original_configs
        seed_mod.DEFAULT_DEPTS = original_depts
