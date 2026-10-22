#!/usr/bin/env python3
"""Fast CMS 一键安装：环境探测 → 后端环境 → 初始化数据库。

前端页面由 FastAPI 用 Jinja2 直接渲染，随 Python 源码一起分发，
没有 Node、没有包管理器、也没有需要预先生成的构建产物，装完即可访问。

用法：
    python scripts/install.py                开发安装（含 dev 依赖）
    python scripts/install.py --prod         发布安装（不装 dev 依赖）
    python scripts/install.py --use-conda    用 Miniconda/Anaconda 环境承载后端
    python scripts/install.py --check        只做环境探测
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_check  # noqa: E402
from env_check import BACKEND_PROBE, IS_WINDOWS, MIN_PYTHON, ROOT, VENV, version_tuple  # noqa: E402

BACKEND = env_check.BACKEND
DEFAULT_CONDA_ENV = "fastcms"


def log(message: str) -> None:
    print(f"[install] {message}", flush=True)


def fail(message: str) -> None:
    raise SystemExit(f"[install] 失败：{message}")


def run(cmd: list[str], cwd: Path) -> None:
    printable = " ".join(str(item) for item in cmd)
    log(f"执行：{printable}")
    result = subprocess.run(
        [str(item) for item in cmd],
        cwd=str(cwd),
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode != 0:
        fail(f"命令退出码 {result.returncode}：{printable}")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def pick_base_python(use_conda: bool, conda_env_name: str) -> tuple[Path, str]:
    """选择承载后端的解释器：已有 venv 优先，其次 conda 环境，最后当前 Python。"""
    existing = venv_python()
    if existing.exists():
        return existing, "venv"
    conda = env_check.find_conda()
    usable = conda.get("envs") or []
    if use_conda:
        if not conda.get("exe"):
            fail("未检测到 Miniconda/Anaconda，无法使用 --use-conda；请安装 Miniconda 或去掉该参数")
        named = next((item for item in usable if item["name"] == conda_env_name), None)
        if named is not None:
            return Path(named["executable"]), f"conda:{named['name']}"
        exe = str(conda["exe"])
        run([exe, "create", "-y", "-n", conda_env_name, f"python={'.'.join(map(str, MIN_PYTHON))}"], ROOT)
        # base 只读时 conda 会把环境写到 ~/.conda/envs，重新探测实际落点而不是猜路径
        created = next((item for item in (env_check.find_conda().get("envs") or []) if item["name"] == conda_env_name), None)
        if created is None:
            fail(f"conda 环境 {conda_env_name} 创建后找不到满足要求的 Python，请检查 conda 输出")
        return Path(created["executable"]), f"conda:{created['name']}"
    if version_tuple(".".join(map(str, sys.version_info[:3]))) >= MIN_PYTHON:
        return Path(sys.executable), "system"
    chosen = next((item for item in usable if version_tuple(item["python"]) >= MIN_PYTHON), None)
    if chosen is None:
        fail("当前 Python 低于 3.12 且没有可用的 conda 环境，请先安装 Python 3.12+ 或 Miniconda")
    return Path(chosen["executable"]), f"conda:{chosen['name']}"


def ensure_backend_python(base_python: Path, source: str) -> Path:
    if source.startswith("conda:"):
        # conda 环境本身已是隔离环境，不再嵌套 venv
        return base_python
    target = venv_python()
    if target.exists():
        return target
    log("创建后端虚拟环境 backend/.venv ...")
    run([str(base_python), "-m", "venv", str(VENV)], ROOT)
    if not target.exists():
        fail("虚拟环境创建失败，请确认 Python 可用且目录有写权限")
    return target


def install_backend(python: Path, dev: bool) -> None:
    probe = subprocess.run(
        [str(python), "-c", f"import {BACKEND_PROBE}"],
        cwd=str(BACKEND),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        log("后端依赖已就绪，跳过安装")
        return
    log("安装后端依赖（首次安装需要几分钟）...")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"], ROOT)
    run([str(python), "-m", "pip", "install", "-e", "backend[dev]" if dev else "backend"], ROOT)


def ensure_env_file() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        log(".env 已存在，保留现有配置")
        return
    shutil.copy2(ROOT / ".env.example", env_file)
    log("已从 .env.example 创建 .env")


def init_database(python: Path) -> None:
    log("迁移并初始化数据库（内置菜单、字典、角色与默认管理员）...")
    run([str(python), "-m", "app.cli", "init"], BACKEND)


def print_summary(python: Path) -> None:
    env = env_check.read_root_env()
    host = env.get("APP_HOST", "127.0.0.1") or "127.0.0.1"
    port = env.get("APP_PORT", "8000") or "8000"
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print("=" * 64)
    print("  安装完成")
    print(f"  后端解释器：{python}")
    print(
        "  启动：python scripts/start.py"
        f"   后台 http://{display_host}:{port}{env_check.admin_base()}"
        f"   前台 http://{display_host}:{port}{env_check.portal_base()}"
    )
    print("  管理员：admin，口令取 .env 的 INITIAL_ADMIN_PASSWORD（留空为开发默认口令）")
    print("  发布前：设置 APP_SECRET_KEY、强 INITIAL_ADMIN_PASSWORD，并把 APP_ENV 改为 prod")
    print("=" * 64)


def main(argv: list[str] | None = None) -> int:
    env_check.setup_console()
    parser = argparse.ArgumentParser(description="Fast CMS 一键安装初始化")
    parser.add_argument("--prod", action="store_true", help="发布安装：不装 dev 依赖")
    parser.add_argument("--use-conda", action="store_true", help="用 Miniconda/Anaconda 环境承载后端")
    parser.add_argument("--conda-env", default=DEFAULT_CONDA_ENV, help=f"--use-conda 使用的环境名（默认 {DEFAULT_CONDA_ENV}）")
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库初始化")
    parser.add_argument("--check", action="store_true", help="只做环境探测")
    parser.add_argument("--recreate", action="store_true", help="删除并重建 backend/.venv")
    args = parser.parse_args(argv)

    report = env_check.build_report()
    print(env_check.render(report))
    if args.check:
        return 0 if report["ready"] else 1
    if not report["ready"]:
        fail(f"环境缺少 {'、'.join(report['blocking'])}，请按上方提示安装后重试")

    if args.recreate and VENV.exists():
        log("删除已有 backend/.venv")
        shutil.rmtree(VENV, ignore_errors=True)

    ensure_env_file()
    base_python, source = pick_base_python(args.use_conda, args.conda_env)
    python = ensure_backend_python(base_python, source)
    # 后续 pip/app.cli 都走这个解释器，conda 场景下把路径告诉安装步骤
    install_backend(python, dev=not args.prod)
    if args.skip_db:
        log("已跳过数据库初始化")
    else:
        init_database(python)
    print_summary(python)
    return 0


if __name__ == "__main__":
    sys.exit(main())
