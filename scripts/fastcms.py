#!/usr/bin/env python3
"""Fast CMS 统一命令行入口。

一条命令覆盖从环境探测到发布升级的整条链路：不依赖 make、不依赖当前目录，
也不要求先记住该用哪个脚本。

    fastcms doctor                    环境探测：Python / conda / 端口 / 数据目录
    fastcms init                      一键安装：依赖 + .env + 数据库
    fastcms init --server             服务端一键安装：production + 0.0.0.0 + 强口令
    fastcms server                    服务端一键部署并启动（无 .env 时自动生成生产配置）
    fastcms dev                       本地开发启动，热重载
    fastcms preview                   按 .env 的 APP_HOST/APP_PORT 启动，关闭热重载
    fastcms test                      独立测试库 + 固定测试口令
    fastcms upgrade                   拉取最新代码 + 更新依赖 + 迁移 + 基线数据
    fastcms check                     全部门禁：lint + test + contract + docs
    fastcms env                       打印配置解析结果（单一事实源）

数据库、备份、定时任务、代码生成等业务命令直接转发到 app.cli：

    fastcms db upgrade                数据库迁移（upgrade / current / check）
    fastcms seed                      写入或同步基线数据
    fastcms sync-rbac                 同步权限与内置角色
    fastcms reset-password admin      重置用户密码
    fastcms backup                    备份数据库
    fastcms restore <路径> --yes      恢复数据库备份
    fastcms unban-ip 10.0.0.0/24      停用封禁规则
    fastcms scheduler                 以独立进程运行调度器
    fastcms gen --table T --module M  根据数据表生成 CRUD 代码
    fastcms export-openapi            导出 OpenAPI 文档
    fastcms check-permissions         校验路由权限码

本入口只做分发与自检：安装逻辑来自 scripts/install.py，启动逻辑来自
scripts/start.py，环境探测来自 scripts/env_check.py，业务命令来自
backend/app/cli.py。没有重复实现任何一步，因此不会与它们各自漂移。

调用方式：

    ./fastcms init --server           Linux / macOS
    fastcms.cmd init --server         Windows
    python scripts/fastcms.py doctor  任意平台（等价）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"

# scripts/ 不是包，先放到 sys.path 上才能 import 同目录的脚本模块
sys.path.insert(0, str(SCRIPTS))

import config_probe  # noqa: E402
import env_check  # noqa: E402
import gate  # noqa: E402
import install  # noqa: E402
import start  # noqa: E402

Handler = Callable[[str, list[str]], int]


# ---------------------------------------------------------------- 子进程执行


def _run(cwd: Path, cmd: list[str]) -> int:
    """执行一条子命令并返回退出码。

    输出直通终端：pip / pytest 的原始输出对用户是最有用的诊断信息，
    不吞掉、不重排，只在前面加一行执行了什么。
    """
    print(f"  $ {' '.join(str(part) for part in cmd)}")
    result = subprocess.run([str(part) for part in cmd], cwd=str(cwd), env=os.environ.copy(), check=False)
    return result.returncode


# ---------------------------------------------------------------- 依赖准备


def _ensure_python(dev: bool = False) -> Path:
    """确保存在一个装齐运行时依赖的解释器（复用 install.py 的选择与创建逻辑）。

    依赖走 requirements.txt（``--deps-only``）：editable 安装需要构建后端
    （setuptools>=69），在受限镜像源下会直接失败；按清单装依赖则始终可用，
    且代码从源码目录运行、升级就是覆盖源码，是项目自己的推荐路径。
    """
    base, source = install.pick_base_python(False, install.DEFAULT_CONDA_ENV, False)
    python = install.ensure_backend_python(base, source, False)
    install.install_backend(python, dev=dev, deps_only=True, user=False, break_system=False)
    install.verify_backend(python)
    return python


def _ensure_dev_tools(python: Path) -> None:
    """门禁工具单独探测。

    运行时依赖齐全不代表开发依赖齐全：用 --prod 或 requirements.txt 装出来的
    环境跑不了 ruff / mypy / pytest，所以这里单独查一次，缺了就补装。
    """
    probe = subprocess.run(
        [str(python), "-c", "import ruff, mypy, pytest"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        return
    print("安装门禁工具（ruff / mypy / pytest）...")
    install.run([str(python), "-m", "pip", "install", "-r", str(install.REQUIREMENTS_DEV)], ROOT)


# ---------------------------------------------------------------- 门禁检查


def _check(_name: str, argv: list[str]) -> int:
    """门禁检查 = make check = CI = release，四者共用 scripts/gate.py 的清单。

    此前这里自己抄了一份 7 步清单（无 security / tsc / 前端行为测试 /
    frontend-imports / escape-sentinel / modules），却在 help 与 README 里声称
    「等价 make check」，是典型的文档与实现漂移。新增门禁现在只需改 gate.py。
    """
    parser = argparse.ArgumentParser(prog="fastcms check", description="全部门禁检查，等价于 make check")
    parser.add_argument("--stop-on-error", action="store_true", help="遇到失败立即停止（默认跑完全部分组再汇总）")
    args = parser.parse_args(argv)

    env_check.setup_console()
    python = _ensure_python()
    _ensure_dev_tools(python)

    started = time.monotonic()
    print("=" * 64)
    print("  门禁检查（scripts/gate.py all）")
    print("=" * 64)
    code = gate.run(list(gate.ORDER), stop_on_error=args.stop_on_error)
    elapsed = time.monotonic() - started
    print("\n" + "=" * 64)
    print(f"  {'全部检查通过' if code == 0 else '检查失败'}（{elapsed:.0f}s）")
    print("=" * 64)
    return code


# ---------------------------------------------------------------- 升级


def _upgrade(_name: str, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fastcms upgrade", description="拉取最新代码并完成升级")
    parser.add_argument("--skip-pull", action="store_true", help="跳过 git pull")
    parser.add_argument("--skip-seed", action="store_true", help="只迁移，不写入基线数据")
    parser.add_argument("--recreate", action="store_true", help="删除并重建 backend/.venv")
    args = parser.parse_args(argv)

    env_check.setup_console()
    print("=" * 64)
    print("  升级")
    print("=" * 64)

    if args.skip_pull:
        print("\n[1/3] 跳过拉取代码")
    elif (ROOT / ".git").exists():
        print("\n[1/3] 拉取最新代码")
        if _run(ROOT, ["git", "pull", "--ff-only"]) != 0:
            print("git pull 失败：请解决冲突后重试，或加 --skip-pull 只升级依赖与数据")
            return 1
    else:
        print("\n[1/3] 不是 git 仓库，跳过拉取（直接在现有源码上升级）")

    print("\n[2/3] 更新依赖")
    if args.recreate and install.venv_python().exists():
        print("删除已有 backend/.venv")
        shutil.rmtree(install.VENV, ignore_errors=True)
    python = _ensure_python()
    _ensure_dev_tools(python)

    print("\n[3/3] 迁移数据库")
    if _run(BACKEND, [str(python), "-m", "app.cli", "db", "upgrade"]) != 0:
        print("数据库迁移失败")
        return 1
    if args.skip_seed:
        print("已跳过基线数据同步（--skip-seed）")
    elif _run(BACKEND, [str(python), "-m", "app.cli", "seed"]) != 0:
        print("基线数据同步失败")
        return 1

    print("\n升级完成，重启服务生效：fastcms server")
    return 0


# ---------------------------------------------------------------- 命令处理器


def _doctor(_name: str, argv: list[str]) -> int:
    return env_check.main(argv)


def _install_cmd(_name: str, argv: list[str]) -> int:
    return install.main(argv)


def _env_cmd(_name: str, argv: list[str]) -> int:
    return config_probe.main(argv)


def _verify_cmd(_name: str, argv: list[str]) -> int:
    """校验发布产物的完整性：归档 SHA256 与 manifest.txt 一致（P2-9）。

    默认在 dist-release/ 下找与 manifest.txt **归档名 + SHA256 同时匹配**的
    fastcms-*.zip / .tar.gz；也可显式指定 --archive 与 --manifest。
    manifest 由 release.py 打包时生成，目标机解压后运行本命令即可确认产物
    在传输/落盘中没有损坏。
    """
    import hashlib
    import re

    parser = argparse.ArgumentParser(
        prog="fastcms verify",
        description="校验发布产物完整性（归档 SHA256 对照 manifest.txt）",
    )
    parser.add_argument("--archive", help="发布包路径（默认 dist-release 下最新的 fastcms-*.zip / .tar.gz）")
    parser.add_argument("--manifest", help="manifest.txt 路径（默认与归档同目录）")
    args = parser.parse_args(argv)

    dist = ROOT / "dist-release"
    explicit = Path(args.archive).resolve() if args.archive else None
    manifest = (Path(args.manifest) if args.manifest else (explicit.parent if explicit else dist) / "manifest.txt").resolve()
    if not manifest.exists():
        print(f"未找到清单：{manifest}")
        return 1

    text = manifest.read_text(encoding="utf-8")
    match = re.search(r"^SHA256[：:]\s*([0-9a-fA-F]{64})", text, re.M)
    if not match:
        print(f"清单缺少 SHA256 行：{manifest}")
        return 1
    expected = match.group(1).lower()
    named = re.search(r"^归档[：:]\s*(\S+)", text, re.M)
    expected_name = named.group(1) if named else None

    if explicit is not None:
        candidates = [explicit]
    else:
        # release.py --format tar.gz 会把当前 zip 转成 tar.gz 并删掉，但目录里
        # 常残留历史 zip；manifest 只有一份且描述的是某一次打包。只按 mtime
        # 取一个会和「manifest 描述另一种格式」撞车，造成误判失败——改成
        # 遍历全部候选，找归档名与摘要都匹配的那一个。
        candidates = [*dist.glob("fastcms-*.zip"), *dist.glob("fastcms-*.tar.gz")]
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        print(
            f"未找到发布包：{dist}/ 下没有 fastcms-*.zip / .tar.gz，"
            "请先运行 python scripts/release.py package（或 make release）"
        )
        return 1

    archive: Path | None = None
    digest = ""
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if expected_name and candidate.name != expected_name:
            continue
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest == expected:
            archive = candidate
            break
    if archive is None:
        print(
            "校验失败：dist-release 下没有归档同时匹配清单记录的归档名与 SHA256 "
            f"（清单归档={expected_name or '未记录'}，候选={[p.name for p in candidates]}）"
        )
        return 1

    print(f"归档：{archive}")
    print(f"清单：{manifest}")
    print(f"期望 SHA256：{expected}")
    print(f"实际 SHA256：{digest}")
    print("校验通过：发布产物完整")
    return 0


def _start(mode: str) -> Handler:
    """把 dev / preview / server / test 收敛成同一个处理器工厂。"""

    def handler(_name: str, argv: list[str]) -> int:
        return start.main([mode, *argv])

    return handler


def _forward(name: str, argv: list[str]) -> int:
    """转发到 app.cli：数据库、备份、定时任务、代码生成等业务命令。"""
    python = _ensure_python()
    return _run(BACKEND, [str(python), "-m", "app.cli", name, *argv])


COMMANDS: dict[str, tuple[str, Handler]] = {
    "doctor": ("环境探测：Python / conda / 端口 / 数据目录", _doctor),
    "init": ("一键安装：依赖 + .env + 数据库（--server 为服务端形态）", _install_cmd),
    "server": ("服务端一键部署：生产配置 + 监听 0.0.0.0 + 打印对外地址", _start("server")),
    "dev": ("本地开发启动，热重载", _start("dev")),
    "serve": ("本地开发启动，热重载（同 dev）", _start("dev")),
    "preview": ("按 .env 的 APP_HOST/APP_PORT 启动，关闭热重载", _start("preview")),
    "test": ("独立测试库 + 固定测试口令", _start("test")),
    "upgrade": ("拉取最新代码 + 更新依赖 + 迁移 + 基线数据", _upgrade),
    "check": ("全部门禁：lint + test + contract + docs（等价 make check）", _check),
    "env": ("打印配置解析结果（单一事实源，--self-test 比对回退实现）", _env_cmd),
    "db": ("转发 app.cli db：upgrade / current / check", _forward),
    "seed": ("转发 app.cli seed：写入或同步基线数据", _forward),
    "sync-rbac": ("转发 app.cli sync-rbac：同步权限与内置角色", _forward),
    "reset-password": ("转发 app.cli reset-password：重置用户密码", _forward),
    "backup": ("转发 app.cli backup：备份数据库", _forward),
    "restore": ("转发 app.cli restore：恢复数据库备份", _forward),
    "unban-ip": ("转发 app.cli unban-ip：停用封禁规则", _forward),
    "scheduler": ("转发 app.cli scheduler：以独立进程运行调度器", _forward),
    "gen": ("转发 app.cli gen：根据数据表生成 CRUD 代码", _forward),
    "export-openapi": ("转发 app.cli export-openapi：导出 OpenAPI 文档", _forward),
    "check-permissions": ("转发 app.cli check-permissions：校验路由权限码", _forward),
    "migrate-registry": (
        "转发 app.cli migrate-registry：干跑导出注册表存量条目为模块清单片段",
        _forward,
    ),
    "verify": ("校验发布产物完整性：归档 SHA256 对照 manifest.txt", _verify_cmd),
}

GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("部署与运行", ("doctor", "init", "server", "dev", "serve", "preview", "test", "upgrade")),
    ("门禁检查", ("check", "env")),
    (
        "数据库与运维",
        ("db", "seed", "sync-rbac", "reset-password", "backup", "restore", "unban-ip", "scheduler"),
    ),
    ("代码生成与契约", ("gen", "export-openapi", "check-permissions", "migrate-registry")),
    ("发布与交付", ("verify",)),
)


def _help() -> str:
    lines = [
        "Fast CMS 统一命令行（不依赖 make，不依赖当前目录）",
        "",
        "用法：fastcms <命令> [参数]        或        python scripts/fastcms.py <命令> [参数]",
    ]
    for title, names in GROUPS:
        lines.extend(("", f"  {title}"))
        for name in names:
            lines.append(f"    fastcms {name:<20} {COMMANDS[name][0]}")
    extra = [name for name in COMMANDS if not any(name in members for _, members in GROUPS)]
    if extra:
        lines.extend(("", "  其他"))
        for name in extra:
            lines.append(f"    fastcms {name:<20} {COMMANDS[name][0]}")
    lines.extend(
        (
            "",
            "  fastcms <命令> --help      查看单个命令的详细参数",
            "  Windows 用 fastcms.cmd，Linux / macOS 用 ./fastcms",
        )
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    env_check.setup_console()
    if not args or args[0] in {"-h", "--help", "help"}:
        print(_help())
        return 0
    if args[0] in {"-V", "--version"}:
        print("Fast CMS 统一命令行（fastcms）")
        return 0

    name, rest = args[0], args[1:]
    entry = COMMANDS.get(name)
    if entry is None:
        print(f"未知命令：fastcms {name}\n")
        print(_help())
        return 2

    return entry[1](name, rest)


if __name__ == "__main__":
    sys.exit(main())
