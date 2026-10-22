"""安装/启动脚本共用模块的回归检查：脚本人工执行才有机会报错，所以在这里静态校验。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import env_check  # noqa: E402


def script_path(name: str) -> Path:
    path = SCRIPTS_DIR / f"{name}.py"
    assert path.exists(), f"缺少启动/安装脚本：{path}"
    return path


def test_api_prefix_matches_backend_normalization() -> None:
    assert env_check.api_prefix({}) == "/api/v1"
    assert env_check.api_prefix({"API_PREFIX": ""}) == "/api/v1"
    assert env_check.api_prefix({"API_PREFIX": " cms/api/ "}) == "/cms/api"
    assert env_check.api_prefix({"API_PREFIX": "/api/v1"}) == "/api/v1"


def test_scripts_only_reference_env_check_attributes_that_exist() -> None:
    """install.py / release.py 靠 env_check 共享常量，写错名字要到生产安装才炸。"""
    pattern = re.compile(r"(?<![\w./])env_check\.([A-Za-z_][A-Za-z0-9_]*)")
    for name in ("install", "release", "start", "env_check"):
        source = script_path(name).read_text(encoding="utf-8")
        for attr in set(pattern.findall(source)):
            assert hasattr(env_check, attr), f"scripts/{name}.py 引用了不存在的 env_check.{attr}"


def test_release_scripts_are_shipped() -> None:
    """打包清单漏掉安装脚本，用户拿到包就无法一键安装。"""
    source = script_path("release").read_text(encoding="utf-8")
    for name in ("scripts/install.py", "scripts/start.py", "scripts/env_check.py", ".env.example"):
        assert f'"{name}"' in source, f"发布清单缺少 {name}"


def test_scripts_do_not_depend_on_node_toolchain() -> None:
    """前端已改为后端直出 HTML，脚本里再出现 pnpm/Node 就是残留的死代码。"""
    pattern = re.compile(r"pnpm|node_modules|UMI_APP_|PORTAL_APP_|npm i -g")
    for name in ("install", "release", "start", "env_check"):
        source = script_path(name).read_text(encoding="utf-8")
        hits = pattern.findall(source)
        assert not hits, f"scripts/{name}.py 仍引用 Node 工具链：{sorted(set(hits))}"


def test_env_check_has_no_node_findings() -> None:
    """环境探测只应关心 Python / conda / 数据库 / 端口。"""
    report = env_check.build_report()
    keys = {item["key"] for item in report["items"]}  # type: ignore[index]
    assert "node" not in keys and "pnpm" not in keys and "frontend" not in keys
    assert report["blocking"] == [] or set(report["blocking"]) <= {"python", "web"}
