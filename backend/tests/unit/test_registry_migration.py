"""``migrate-registry`` 干跑命令与注册表分组的回归测试。

A-1：权限/页面/菜单是「存量集中 + 增量走清单」双轨制。本测试钉死两条：

1. ``build_registry_candidates()`` 把存量条目**全部分组**（不漏、不重复），
   且分组键不含空占位符（菜单目录节点也要能归到真实模块）；
2. ``fastcms migrate-registry`` 干跑可执行、打印片段、不落盘。

防止的漂移：分组键改坏导致某个模块的候选片段静默消失，或命令报错。
"""

from __future__ import annotations

from app.bootstrap.menus import MENU_TREE, iter_menus
from app.bootstrap.permissions import PERMISSIONS
from app.bootstrap.registry_migration import build_registry_candidates
from app.web.router import PAGES


def test_grouping_covers_every_baseline_entry() -> None:
    """三张注册表的存量条目必须全部进入某个分组，一个不落。"""
    candidates = build_registry_candidates()

    grouped_perms = sum(len(block["permissions"]) for block in candidates.values())
    grouped_pages = sum(len(block["pages"]) for block in candidates.values())
    grouped_menus = sum(len(block["menus"]) for block in candidates.values())
    total_menu_nodes = sum(1 for _node, _parent in iter_menus(MENU_TREE))

    assert grouped_perms == len(PERMISSIONS) == 98
    assert grouped_pages == len(PAGES) == 31
    assert grouped_menus == total_menu_nodes


def test_no_placeholder_group() -> None:
    """分组键不能出现 ``?`` 占位符——那意味着有条目归不进任何模块。"""
    assert "?" not in build_registry_candidates()


def test_system_module_has_expected_entries() -> None:
    """system 模块应聚集权限与页面主体（抽样验证分组归属正确）。"""
    block = build_registry_candidates()["system"]
    codes = {item["code"] for item in block["permissions"]}
    assert "system:user:list" in codes
    assert "system:role:list" in codes
    pages = {item["path"] for item in block["pages"]}
    assert "/system/user" in pages
    assert "/system/dict" in pages


def test_migrate_registry_runs_dry_and_prints(capsys) -> None:
    """干跑命令必须可执行、打印片段与合计，且不写任何文件。"""
    from app import cli

    cli.main(["migrate-registry"])
    out = capsys.readouterr().out
    assert "permissions=(" in out
    assert "pages=(" in out
    assert "menus=(" in out
    assert "未写任何文件" in out
    assert "模块 system" in out
