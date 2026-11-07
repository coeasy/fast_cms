#!/usr/bin/env python3
"""Fast CMS 一键安装：环境探测 → 后端环境 → 初始化数据库。

前端页面由 FastAPI 用 Jinja2 直接渲染，随 Python 源码一起分发，
没有 Node、没有包管理器、也没有需要预先生成的构建产物，装完即可访问。

两种承载方式：
    默认        创建 backend/.venv（隔离，推荐）
    --system    不建 venv，把依赖装进当前解释器或指定 conda 环境

用法：
    python scripts/install.py                    开发安装（含 dev 依赖，创建 venv）
    python scripts/install.py --server           服务端部署（production + 0.0.0.0 + 强口令）
    python scripts/install.py --server --http    同上，但按明文 HTTP 访问（无 HTTPS 时用）
    python scripts/install.py --prod             发布安装（不装 dev 依赖）
    python scripts/install.py --system           装进当前环境，不创建 venv
    python scripts/install.py --system --user    装进当前用户的 site-packages
    python scripts/install.py --use-conda        用 Miniconda/Anaconda 环境承载后端
    python scripts/install.py --deps-only        只装 requirements.txt，不做 editable 安装
    python scripts/install.py --check            只做环境探测
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_check
from env_check import IS_WINDOWS, MIN_PYTHON, ROOT, VENV, version_tuple

BACKEND = env_check.BACKEND
DEFAULT_CONDA_ENV = "fastcms"
# PEP 668（Debian 12 / Ubuntu 23.04+ 等）会直接拒绝写入系统 site-packages
PEP668_MARKERS = ("externally-managed-environment", "externally managed")
REQUIREMENTS = BACKEND / "requirements.txt"
REQUIREMENTS_DEV = BACKEND / "requirements-dev.txt"


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


def pip_flags(user: bool, break_system: bool) -> list[str]:
    """--system 场景下的附加参数：装到用户目录 / 显式覆盖 PEP 668 保护。"""
    flags: list[str] = []
    if user:
        flags.append("--user")
    if break_system:
        flags.append("--break-system-packages")
    return flags


def run_pip(cmd: list[str], user: bool, break_system: bool) -> None:
    """执行 pip 并捕获输出：PEP 668 的报错需要翻译成人能看懂的三选一。"""
    del user
    printable = " ".join(str(item) for item in cmd)
    log(f"执行：{printable}")
    result = subprocess.run(
        [str(item) for item in cmd],
        cwd=str(ROOT),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = result.stdout or ""
    if output:
        print(output if output.endswith("\n") else f"{output}\n", end="", flush=True)
    if result.returncode == 0:
        return
    lowered = output.lower()
    if any(marker in lowered for marker in PEP668_MARKERS) and not break_system:
        fail(
            "当前 Python 受系统包管理保护（PEP 668），拒绝直接写入 site-packages。三个选择：\n"
            "  1) 装进虚拟环境（推荐）：python scripts/install.py\n"
            "  2) 装进用户目录：      python scripts/install.py --system --user\n"
            "  3) 确认要改动系统 Python：python scripts/install.py --system --break-system-packages"
        )
    fail(f"命令退出码 {result.returncode}：{printable}")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def pick_base_python(use_conda: bool, conda_env_name: str, use_system: bool) -> tuple[Path, str]:
    """选择承载后端的解释器。

    --system 时不看已有 venv（用户明确要求用当前环境）；
    否则已有 venv 优先，其次 conda 环境，最后当前 Python。
    """
    if not use_system:
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


def ensure_backend_python(base_python: Path, source: str, use_system: bool) -> Path:
    if use_system or source.startswith("conda:"):
        # 用户要求不建 venv，或 conda 环境本身已是隔离环境，不再嵌套 venv
        return base_python
    target = venv_python()
    if target.exists():
        return target
    log("创建后端虚拟环境 backend/.venv ...")
    run([str(base_python), "-m", "venv", str(VENV)], ROOT)
    if not target.exists():
        fail("虚拟环境创建失败，请确认 Python 可用且目录有写权限")
    return target


def install_backend(python: Path, dev: bool, deps_only: bool, user: bool, break_system: bool) -> None:
    missing = env_check.probe_dependencies(python)
    if not missing:
        log("后端依赖已就绪，跳过安装")
        return
    log(f"缺少 {len(missing)} 项依赖：{'、'.join(missing)}")
    log("安装后端依赖（首次安装需要几分钟）...")
    run_pip([str(python), "-m", "pip", "install", "--upgrade", "pip", *pip_flags(user, break_system)], user, break_system)
    if deps_only:
        # 只装依赖，不把项目本身装进 site-packages：代码始终从源码目录运行，
        # 模板与静态资源不需要依赖 package-data，升级就是覆盖源码，最不容易出错。
        target = REQUIREMENTS_DEV if dev else REQUIREMENTS
        if not target.exists():
            fail(f"缺少 {target}，无法使用 --deps-only")
        run_pip(
            [str(python), "-m", "pip", "install", "-r", str(target), *pip_flags(user, break_system)],
            user,
            break_system,
        )
    else:
        spec = "backend[dev]" if dev else "backend"
        run_pip([str(python), "-m", "pip", "install", "-e", spec, *pip_flags(user, break_system)], user, break_system)


def verify_backend(python: Path) -> None:
    """装完必须复核：pip 返回 0 不等于所有包都能 import（缓存轮子损坏、装错解释器都遇到过）。"""
    missing = env_check.probe_dependencies(python)
    if missing:
        fail(
            f"安装后仍缺少依赖：{'、'.join(missing)}。\n"
            f"  可手动重试：{python} -m pip install -r {REQUIREMENTS}\n"
            "  若是网络问题，请检查 pip 源（--index-url）后重新执行本脚本"
        )
    log("依赖校验通过")


def ensure_env_file() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        log(".env 已存在，保留现有配置")
        return
    shutil.copy2(ROOT / ".env.example", env_file)
    log("已从 .env.example 创建 .env")


def patch_env_file(updates: dict[str, str]) -> None:
    """就地更新 .env 的指定键，其余行（含注释）原样保留。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy2(ROOT / ".env.example", env_file)
        log("已从 .env.example 创建 .env")
    lines = env_file.read_text(encoding="utf-8").splitlines()
    pending = dict(updates)
    merged: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key and key in pending:
            merged.append(f"{key}={pending.pop(key)}")
        else:
            merged.append(line)
    if pending:
        merged.append("")
        merged.extend(f"{key}={value}" for key, value in pending.items())
    env_file.write_text("\n".join(merged) + "\n", encoding="utf-8")


def generate_password(length: int = 16) -> str:
    """生成满足口令策略的随机口令：同时含字母与数字，且避开易混淆字符。"""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    # 与 app.cli 同口径：拒绝采样必须有上限，避免安装脚本在异常字母表下死循环。
    for _ in range(64):
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(char.islower() for char in candidate) and any(char.isdigit() for char in candidate):
            return candidate
    return "a2" + "x" * (length - 2)


def configure_server_env(admin_password: str | None, allow_http: bool) -> tuple[str, bool]:
    """把 .env 调成「其他机器能访问」的形态，返回 (管理员口令, 是否为本次生成)。

    只改必需的几项，其余全部走代码默认值——.env 里多写一行默认值，只会让
    「这台机器到底改了什么」变得不可读。
    """
    existing = env_check.read_root_env()
    password = (admin_password or existing.get("INITIAL_ADMIN_PASSWORD") or "").strip()
    generated = False
    if not password:
        password = generate_password()
        generated = True

    updates: dict[str, str] = {
        "APP_ENV": "production",
        "APP_HOST": "0.0.0.0",
        "INITIAL_ADMIN_PASSWORD": password,
        # 对外提供服务时把接口文档收起来；需要时改回 true 即可
        "APP_DOCS_ENABLED": "false",
    }
    if not (existing.get("APP_SECRET_KEY") or "").strip():
        updates["APP_SECRET_KEY"] = secrets.token_urlsafe(48)
    if allow_http:
        # Secure Cookie 在明文 HTTP 下根本不会被浏览器保存，必须显式关掉
        updates["AUTH_COOKIE_SECURE"] = "false"

    patch_env_file(updates)
    return password, generated


def print_server_notes(password: str, generated: bool, allow_http: bool) -> None:
    env = env_check.read_root_env()
    port = env.get("APP_PORT", "8000") or "8000"
    admin_path = env_check.admin_base(env).rstrip("/")
    addresses = env_check.local_ipv4_addresses()
    print("=" * 64)
    print("  服务端配置已写入 .env")
    print("    APP_ENV=production        强制强口令，Cookie 按 HTTPS 规则下发")
    print("    APP_HOST=0.0.0.0          允许其他机器访问")
    print("    APP_DOCS_ENABLED=false    关闭 /docs 与 /openapi.json（需要时改回 true）")
    print(f"    管理员：admin / {password}" + ("（本次自动生成，只显示这一次）" if generated else ""))
    print("-" * 64)
    print("  启动：python scripts/start.py server")
    if addresses:
        print("  其他机器访问：")
        for ip in addresses:
            print(f"    http://{ip}:{port}{admin_path}")
    else:
        print(f"  其他机器访问：http://<本机IP>:{port}{admin_path}")
    print("-" * 64)
    if allow_http:
        print("  ! 已按 HTTP 访问配置 AUTH_COOKIE_SECURE=false。")
        print("    上公网前请改用 HTTPS 反向代理，并把该项改回 true（或删掉该行走自动）。")
    else:
        print("  ! 当前按 HTTPS 假设下发 Secure Cookie。若你直接用 http:// 访问（没有反向代理），")
        print("    请重新执行：python scripts/install.py --server --http")
        print("    否则浏览器不保存刷新令牌，登录 15 分钟后会被踢出。")
    print("=" * 64)


def init_database(python: Path) -> None:
    log("迁移并初始化数据库（内置菜单、字典、角色与默认管理员）...")
    run([str(python), "-m", "app.cli", "init"], BACKEND)


def print_summary(python: Path, source: str) -> None:
    env = env_check.read_root_env()
    host = env.get("APP_HOST", "127.0.0.1") or "127.0.0.1"
    port = env.get("APP_PORT", "8000") or "8000"
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    backend_dir = ROOT / "backend"
    print("=" * 64)
    print("  安装完成")
    print(f"  后端解释器：{python}（{source}）")
    print(
        "  启动：python scripts/start.py"
        f"   后台 http://{display_host}:{port}{env_check.admin_base()}"
        f"   前台 http://{display_host}:{port}{env_check.portal_base()}"
    )
    print("  管理员：admin，口令取 .env 的 INITIAL_ADMIN_PASSWORD（留空为开发默认口令）")
    if source != "venv":
        print("-" * 64)
        print("  未使用虚拟环境，也可以直接手动拉起（不经过启动器）：")
        print(f"    cd {backend_dir}")
        print(f"    {python} -m uvicorn app.main:app --host {host} --port {port}")
    print("-" * 64)
    print("  发布前：设置 APP_SECRET_KEY、强 INITIAL_ADMIN_PASSWORD，并把 APP_ENV 改为 prod")
    print("=" * 64)


def main(argv: list[str] | None = None) -> int:
    env_check.setup_console()
    parser = argparse.ArgumentParser(description="Fast CMS 一键安装初始化")
    parser.add_argument("--prod", action="store_true", help="发布安装：不装 dev 依赖")
    parser.add_argument("--use-conda", action="store_true", help="用 Miniconda/Anaconda 环境承载后端")
    parser.add_argument("--conda-env", default=DEFAULT_CONDA_ENV, help=f"--use-conda 使用的环境名（默认 {DEFAULT_CONDA_ENV}）")
    parser.add_argument(
        "--system",
        action="store_true",
        help="不创建 backend/.venv，把依赖装进当前解释器（或 --use-conda 指定的环境）",
    )
    parser.add_argument("--user", action="store_true", help="配合 --system：装进当前用户的 site-packages")
    parser.add_argument(
        "--break-system-packages",
        action="store_true",
        help="配合 --system：显式允许写入受 PEP 668 保护的系统 Python（有风险，确认后再用）",
    )
    parser.add_argument(
        "--deps-only",
        action="store_true",
        help="只按 backend/requirements.txt 装依赖，不做 editable 安装（代码始终从源码目录运行）",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="服务端部署：把 .env 配成 production + 0.0.0.0 + 强口令，可被其他机器访问",
    )
    parser.add_argument("--admin-password", default=None, help="配合 --server：指定管理员初始口令（留空则随机生成）")
    parser.add_argument(
        "--http",
        action="store_true",
        help="配合 --server：按明文 HTTP 访问配置（AUTH_COOKIE_SECURE=false），没有 HTTPS 时必须加",
    )
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库初始化")
    parser.add_argument("--check", action="store_true", help="只做环境探测")
    parser.add_argument("--recreate", action="store_true", help="删除并重建 backend/.venv")
    args = parser.parse_args(argv)

    if args.user and not args.system:
        parser.error("--user 需要与 --system 一起使用（否则请让 install.py 创建 venv）")
    if args.break_system_packages and not args.system:
        parser.error("--break-system-packages 需要与 --system 一起使用")
    if args.http and not args.server:
        parser.error("--http 需要与 --server 一起使用")
    if args.admin_password and not args.server:
        parser.error("--admin-password 需要与 --server 一起使用（其他模式的口令取自 .env）")

    report = env_check.build_report()
    print(env_check.render(report))
    if args.check:
        return 0 if report["ready"] else 1
    if not report["ready"]:
        fail(f"环境缺少 {'、'.join(report['blocking'])}，请按上方提示安装后重试")

    if args.recreate and VENV.exists():
        if args.system:
            log("--system 模式不创建 venv，忽略 --recreate")
        else:
            log("删除已有 backend/.venv")
            shutil.rmtree(VENV, ignore_errors=True)

    password = ""
    generated = False
    if args.server:
        password, generated = configure_server_env(args.admin_password, args.http)
    else:
        ensure_env_file()
    base_python, source = pick_base_python(args.use_conda, args.conda_env, args.system)
    python = ensure_backend_python(base_python, source, args.system)
    install_backend(
        python,
        dev=not (args.prod or args.server),
        deps_only=args.deps_only,
        user=args.user,
        break_system=args.break_system_packages,
    )
    verify_backend(python)
    if args.skip_db:
        log("已跳过数据库初始化")
    else:
        init_database(python)
    if args.server:
        print_server_notes(password, generated, args.http)
    else:
        print_summary(python, source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
