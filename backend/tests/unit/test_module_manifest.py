"""模块清单与分层约定的回归测试。

对应 ``app/core/module.py`` 的清单机制与 ``scripts/check_modules.py`` 的门禁。
单测覆盖的是「约定本身」——清单能被发现、顺序稳定、底座不反向依赖业务模块、
模块间依赖不越界；路径级细节（OpenAPI 是否挂载）由门禁脚本负责。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent / "scripts"))

import check_modules  # noqa: E402

from app.core.module import ModuleSpec, discover_modules  # noqa: E402


def test_discover_returns_manifests_in_order() -> None:
    specs = discover_modules()
    assert len(specs) >= 10, "模块清单数量异常，可能漏写 module.py"
    orders = [spec.order for spec in specs]
    assert orders == sorted(orders), "模块注册顺序必须按 order 升序"
    names = [spec.name for spec in specs]
    assert len(names) == len(set(names)), f"模块名重复：{names}"


def test_every_manifest_declares_router() -> None:
    for spec in discover_modules():
        assert isinstance(spec, ModuleSpec)
        assert spec.routers, f"{spec.name} 没有声明任何 router"
        assert spec.description, f"{spec.name} 缺少 description（清单要能自解释）"


def test_manifest_name_matches_directory() -> None:
    for package_dir in check_modules.iter_package_dirs():
        manifest = package_dir / "module.py"
        assert manifest.exists(), f"{package_dir} 缺少 module.py"
        spec = next(s for s in discover_modules() if s.name == package_dir.name)
        assert spec.name == package_dir.name


def test_core_and_kernel_do_not_import_modules() -> None:
    """底座分层：core / kernel 反向依赖业务模块是最典型的架构腐化。"""
    offenders: list[str] = []
    app_dir = BACKEND / "app"
    for layer in ("core", "kernel"):
        for path in (app_dir / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.modules."):
                    offenders.append(f"{path.relative_to(app_dir).as_posix()} -> {node.module}")
    assert not offenders, "core/kernel 不得依赖业务模块：" + ", ".join(offenders)


def test_module_dependencies_stay_within_allowlist() -> None:
    problems: list[str] = []
    check_modules.check_layering(problems)
    assert not problems, "模块依赖越界：\n" + "\n".join(problems)


def test_allowlist_entries_have_reasons() -> None:
    """白名单必须是架构决策记录，不能变成无理由豁免。"""
    for edge, reason in check_modules.ALLOWED_MODULE_EDGES.items():
        assert len(reason) >= 6, f"{edge} 的理由太短，写清楚为什么必须依赖"
        assert len(edge) == 2
