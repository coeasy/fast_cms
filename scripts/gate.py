#!/usr/bin/env python3
"""统一质量门禁清单：make check、CI、release 共用同一组门禁步骤。

此前三处各自硬编码：release 缺 mypy / 权限校验 / 文档校验，CI 缺文档校验，
发布包可能跳过静态类型与文档检查。本模块把门禁抽成单一清单，三个入口
都走这里，失败信息一致。

用法：
    python scripts/gate.py lint       只跑 ruff + mypy
    python scripts/gate.py test       只跑 pytest
    python scripts/gate.py contract   契约校验（OpenAPI / 权限 / 前后端连通）
    python scripts/gate.py docs       文档命令存在性校验
    python scripts/gate.py all        以上全部（make check / release / CI 共用）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env_check import BACKEND, IS_WINDOWS, ROOT, VENV


def _venv_python() -> str:
    """优先 backend/.venv 的解释器；CI 直接 pip install 无 venv 时退回当前解释器。"""
    target = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    if target.exists():
        return str(target)
    return sys.executable


def _find_node() -> str | None:
    """优先本机 PATH 的 node，其次 WorkBuddy 托管的 node；都没有返回 None。"""
    node = shutil.which("node")
    if node:
        return node
    managed = Path.home() / ".workbuddy-ai" / "binaries" / "node" / "versions"
    for entry in sorted(managed.glob("*/node.exe"), reverse=True):
        return str(entry)
    return None


def _tsc_step() -> tuple[str, list[str], Path] | None:
    """构造 tsc 类型检查步骤（Phase 10-2：core/ui 零类型错误）。

    typescript 是**纯开发期工具**：只存在于本机 node_modules（已 gitignore），
    不进 pyproject、不进任何运行时依赖。因此本地没装时降级为「跳过并提示」，
    而 CI 的 lint job 会先 `npm install --no-save typescript@5` 再跑门禁，
    保证 CI 上始终硬校验。
    """
    node = _find_node()
    tsc_js = ROOT / "node_modules" / "typescript" / "bin" / "tsc"
    if not (node and tsc_js.exists()):
        return None
    return ("frontend-tsc", [node, str(tsc_js), "--noEmit", "-p", "tsconfig.json"], ROOT)


PY = _venv_python()
# 仓库脚本（scripts/*.py）里有三个要 import app.*（check_contract / check_api_coverage /
# check_modules），必须用装了后端依赖的解释器跑。此前用 sys.executable，于是
# 「用系统 python 调 gate.py」会在 contract 组直接 ModuleNotFoundError: pydantic，
# 而用 venv python 调就正常——同一份代码两种结果，是典型的环境耦合缺陷。
SCRIPT_PY = PY

# 每组门禁：(label, cmd, cwd)。顺序即执行顺序，任一步失败即中断本组。
_TSC = _tsc_step()
GROUPS: dict[str, list[tuple[str, list[str], Path]]] = {
    "lint": [
        ("ruff", [PY, "-m", "ruff", "check", "app", "tests", "../scripts"], BACKEND),
        ("mypy", [PY, "-m", "mypy", "app"], BACKEND),
        *([_TSC] if _TSC else []),
    ],
    "test": [
        # 不再追加 -q：pyproject 已设 addopts="-q"，再加一次会变成 -qq，
        # 连「364 passed」的统计行都被压掉，失败时反而更难定位。
        ("pytest", [PY, "-m", "pytest"], BACKEND),
        # 前端纯逻辑行为测试（node --test，零第三方依赖）。node 缺失即失败，
        # 因为「前端行为测试 ≥ 30 用例」是总体验收硬指标，不可降级为警告。
        # 用带引号的 glob 让 node 自己展开（subprocess 不经 shell，不会展开 *）。
        ("frontend-node-test", [_find_node() or "node", "--test", "tests/frontend/*.test.mjs"], BACKEND),
    ],
    "contract": [
        ("openapi", [PY, "-m", "app.cli", "export-openapi", "--output", "../openapi.json"], BACKEND),
        ("permissions", [PY, "-m", "app.cli", "check-permissions"], BACKEND),
        ("contract", [SCRIPT_PY, str(ROOT / "scripts" / "check_contract.py")], ROOT),
        ("coverage", [SCRIPT_PY, str(ROOT / "scripts" / "check_api_coverage.py"), "--strict"], ROOT),
        ("frontend-imports", [SCRIPT_PY, str(ROOT / "scripts" / "check_frontend_imports.py")], ROOT),
        ("escape-sentinel", [SCRIPT_PY, str(ROOT / "scripts" / "check_escape_sentinel.py")], ROOT),
        ("modules", [SCRIPT_PY, str(ROOT / "scripts" / "check_modules.py")], ROOT),
    ],
    "docs": [
        ("docs", [SCRIPT_PY, str(ROOT / "scripts" / "check_docs.py")], ROOT),
    ],
    "security": [
        ("pip-audit", [SCRIPT_PY, str(ROOT / "scripts" / "check_security.py")], ROOT),
    ],
}

ORDER = ("lint", "test", "contract", "docs", "security")


def run(groups: list[str], *, stop_on_error: bool = False) -> int:
    in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
    if not _TSC and "lint" in groups:
        if in_ci:
            # 本地可以没有 typescript（纯开发期工具，CI 才装），但 CI 上缺失
            # 只能说明 CI 步骤装错了目录；静默跳过会让「类型门禁」名存实亡。
            print(
                "!! [lint] frontend-tsc 在 CI 中不可跳过：仓库根 node_modules/typescript "
                "不存在，请检查 npm install 的工作目录",
                flush=True,
            )
            return 1
        print(
            "==> [lint] frontend-tsc：跳过（未安装 typescript；"
            "执行 npm install --no-save typescript@5 后启用，CI 上始终启用）",
            flush=True,
        )
    failed = False
    for group in groups:
        for label, cmd, cwd in GROUPS[group]:
            print(f"==> [{group}] {label}：{' '.join(cmd)}", flush=True)
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run([str(item) for item in cmd], cwd=str(cwd), env=env, check=False)
            if result.returncode != 0:
                print(f"!! 门禁失败 [{group}] {label}（退出码 {result.returncode}）", flush=True)
                if stop_on_error:
                    return 1
                failed = True
                break
    if failed:
        print("!! 质量门禁未全部通过", flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统一质量门禁")
    parser.add_argument(
        "target",
        nargs="?",
        choices=("lint", "test", "contract", "docs", "security", "all"),
        default="all",
    )
    args = parser.parse_args(argv)
    groups = list(ORDER) if args.target == "all" else [args.target]
    return run(groups)


if __name__ == "__main__":
    sys.exit(main())
