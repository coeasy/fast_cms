#!/usr/bin/env python3
"""Fast CMS 发布前检查与打包。

前端页面是后端源码的一部分（Jinja2 模板 + 原生 JS），不需要构建，
因此发布门禁只剩后端静态检查、单元/集成测试与契约校验。

用法：
    python scripts/release.py check      只跑质量门禁（契约 + 测试）
    python scripts/release.py package    门禁通过后生成发布包
    python scripts/release.py all        两者都做
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_check  # noqa: E402
from env_check import BACKEND, IS_WINDOWS, ROOT, VENV  # noqa: E402

DIST = ROOT / "dist-release"
# 发布包内容：源码（含 HTML 模板与静态资源）+ 启动安装脚本，不含 venv、数据库与上传文件。
# backend/migrations 必须带上：alembic.ini 的 script_location 指向它，
# 少了它全新机器上 `app.cli init` 会直接报 "Path doesn't exist: .../migrations"，
# 建不出任何表——发布包看起来完整，其实一步都跑不起来。
INCLUDE_DIRS = (
    "backend/app",
    "backend/migrations",
    # 测试一起打包：它们固化的是「不许再犯」的约束（multipart 不能死锁、
    # 每条限流规则都要命中端点、API Key 必须真能认证），拿到包的人能自己复跑门禁
    "backend/tests",
    "backend/pyproject.toml",
    "backend/alembic.ini",
)
INCLUDE_FILES = (
    "scripts/start.py",
    "scripts/install.py",
    "scripts/env_check.py",
    "scripts/release.py",
    "scripts/check_contract.py",
    "scripts/check_api_coverage.py",
    "openapi.json",
    ".env.example",
    "Makefile",
    "README.md",
    "start.sh",
    "start.cmd",
    "install.sh",
    "install.cmd",
    "docker/Dockerfile",
    "docker/compose.yml",
    "docker/entrypoint.sh",
)
INCLUDE_DOCS = tuple(f"docs/{name}" for name in sorted(os.listdir(ROOT / "docs")) if name.endswith(".md"))
EXCLUDE_PARTS = ("__pycache__", ".pytest_cache", ".ruff_cache", "mypy_cache")

# 发布配置检查项。这些全部有可用默认值（见 backend/app/core/config.py），
# 本地零配置就能跑起来；这里只在生产环境下对「没给值」的项提示，不阻断。
# "<非空>" 是特殊标记：该项留空时有兜底值（自动生成/内置口令），只在生产环境算风险。
# 值是「可接受集合」：APP_ENV 同时认 prod 与 production，与 PRODUCTION_ENVS 对齐，
# 只认 prod 的话把 production 当违例报，等于教用户改错配置。
RELEASE_CHECKS = {
    "APP_ENV": ("prod", "production"),
    "APP_SECRET_KEY": "<非空>",
    "INITIAL_ADMIN_PASSWORD": "<非空>",
    "RATE_LIMIT_ENABLED": ("true",),
    "AUTO_MIGRATE": ("false",),
}

# 与 backend/app/core/config.py 的字段默认值保持一致：值没写时是「用默认」，
# 不是「没配」。默认值本身就符合发布建议，拿空字符串去比才会误报。
# 只收「有发布建议且默认值就是建议值」的项；APP_SECRET_KEY 与
# INITIAL_ADMIN_PASSWORD 走 <非空> 分支（兜底值分别是自动生成的口令与众所周知的
# 开发口令，本身不是发布建议），不放进这张表。
RELEASE_DEFAULTS = {
    "APP_ENV": "dev",
    "RATE_LIMIT_ENABLED": "true",
    "AUTO_MIGRATE": "false",
    "APP_WORKERS": "1",
    "SCHEDULER_MODE": "embedded",
    "REDIS_ENABLED": "false",
}

# pydantic-settings 对 bool 字段接受的写法，比 env_check 直接字符串比较宽得多，
# 不归一化的话 RATE_LIMIT_ENABLED=1 这种完全合法的配置也会被误报成「建议 true」。
_BOOL_TRUE = {"1", "true", "yes", "on", "t", "y"}
_BOOL_FALSE = {"0", "false", "no", "off", "f", "n"}


def _as_bool(raw: str) -> str | None:
    value = raw.strip().lower()
    if value in _BOOL_TRUE:
        return "true"
    if value in _BOOL_FALSE:
        return "false"
    return None


def log(message: str) -> None:
    print(f"[release] {message}", flush=True)


def fail(message: str) -> None:
    raise SystemExit(f"[release] 失败：{message}")


def python_bin() -> Path:
    target = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    if not target.exists():
        fail("未找到 backend/.venv，请先执行 `python scripts/install.py`")
    return target


def step(cmd: list[str], cwd: Path) -> None:
    label = " ".join(str(item) for item in cmd)
    log(f"执行：{label}")
    env = os.environ.copy()
    # 子进程输出里有中文与符号，固定 UTF-8 以免在中文 Windows 管道上抛编码错误
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([str(item) for item in cmd], cwd=str(cwd), env=env, check=False)
    if result.returncode != 0:
        fail(f"步骤失败（退出码 {result.returncode}）：{label}")


def read_version() -> str:
    pyproject = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    for line in pyproject.splitlines():
        stripped = line.strip()
        if stripped.startswith("version ="):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def run_gate() -> None:
    python = python_bin()
    report = env_check.build_report()
    print(env_check.render(report))
    if not report["ready"]:
        fail(f"环境缺少 {'、'.join(report['blocking'])}")

    step([python, "-m", "ruff", "check", "app", "tests", "../scripts"], BACKEND)
    step([python, "-m", "pytest", "-q"], BACKEND)
    step([python, "-m", "app.cli", "export-openapi", "--output", "../openapi.json"], BACKEND)
    step([python, str(ROOT / "scripts" / "check_contract.py")], ROOT)
    step([python, str(ROOT / "scripts" / "check_api_coverage.py"), "--strict"], ROOT)
    log("质量门禁全部通过")


def effective_env() -> dict[str, str]:
    """生效中的环境变量：``.env`` 打底，真实环境变量覆盖。

    只读 ``.env`` 文件是个坑：Docker / systemd / CI 里没人写 ``.env``，
    配置全在环境变量里，于是「明明已经用环境变量配好了」也照样报缺失。
    优先级与 pydantic-settings 保持一致——环境变量 > .env 文件。
    """
    values = env_check.read_root_env()
    watched = set(RELEASE_CHECKS) | {"DB_URL", "APP_WORKERS", "REDIS_ENABLED", "SCHEDULER_MODE"}
    values.update({key: value for key, value in os.environ.items() if key.upper() in watched})
    return values


def config_warnings() -> list[str]:
    env = effective_env()
    env_name = (env.get("APP_ENV") or RELEASE_DEFAULTS["APP_ENV"]).strip().lower()
    production = env_name in {"prod", "production"}
    warnings: list[str] = []

    for key, expected in RELEASE_CHECKS.items():
        raw = (env.get(key) or "").strip()
        if expected == "<非空>":
            # 未设置不等于不可用：APP_SECRET_KEY 会自动生成并落盘 data/.secret，
            # INITIAL_ADMIN_PASSWORD 有内置默认值。两者只在生产环境构成风险，
            # 本地零配置启动是本项目的设计目标，不该为此报警。
            if not raw and production:
                warnings.append(f"{key} 未设置（生产环境必须显式提供）")
            continue
        acceptable = expected if isinstance(expected, tuple) else (expected,)
        # 没写配置时生效的是默认值，拿默认值去比，别拿空字符串去比
        value = raw or RELEASE_DEFAULTS.get(key, "")
        if key in {"RATE_LIMIT_ENABLED", "AUTO_MIGRATE", "REDIS_ENABLED"}:
            normalized = _as_bool(value)
            if normalized is None:
                warnings.append(f"{key}={value!r} 不是合法的布尔值（true/false）")
                continue
            value = normalized
        if value.lower() not in acceptable:
            warnings.append(f"{key}={value or '空'}，发布建议 {'/'.join(acceptable)}")

    db_url = (env.get("DB_URL") or "").strip()
    if db_url and not db_url.startswith("sqlite"):
        return warnings

    # SQLite 是默认数据库，单进程下完全可用，不该拿「建议」当「问题」报。
    # 真正会出事的只有两种：多 worker 并发写同一文件，或调度器跑在独立进程里
    # （Web 与 scheduler 两个进程同时写 job 日志）。这两种之外一律不提示。
    workers = (env.get("APP_WORKERS") or RELEASE_DEFAULTS["APP_WORKERS"]).strip()
    scheduler = (env.get("SCHEDULER_MODE") or RELEASE_DEFAULTS["SCHEDULER_MODE"]).strip().lower()
    multi_process = workers != "1" or scheduler == "standalone"
    if multi_process:
        redis = _as_bool(env.get("REDIS_ENABLED") or RELEASE_DEFAULTS["REDIS_ENABLED"]) == "true"
        if not redis:
            warnings.append("APP_WORKERS>1 或调度器独立运行，但未启用 Redis，字典/参数缓存会在各进程间不一致")
        if not db_url.startswith("mysql"):
            warnings.append("多进程写库请把 DB_URL 改为 MySQL，SQLite 同文件并发写会 database is locked")
    return warnings


def matches(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return False
    if rel in INCLUDE_FILES or rel in INCLUDE_DOCS:
        return True
    return any(rel == item or rel.startswith(f"{item}/") for item in INCLUDE_DIRS)


def collect() -> list[Path]:
    files: list[Path] = []
    for base in ("backend", "scripts", "docs", "docker"):
        root = ROOT / base
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and matches(path):
                files.append(path)
    for name in (item for item in INCLUDE_FILES if "/" not in item):
        candidate = ROOT / name
        if candidate.is_file():
            files.append(candidate)
    return files


def write_manifest(files: list[Path], version: str, archive: Path, digest: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"Fast CMS {version}",
        f"打包时间：{stamp}",
        f"打包主机：{platform.system()} {platform.release()} ({platform.machine()})",
        f"文件数：{len(files)}",
        f"归档：{archive.name}",
        f"SHA256：{digest}",
        "",
        "发布配置检查：",
    ]
    warnings = config_warnings()
    lines.append("  全部符合建议" if not warnings else "  " + "\n  ".join(f"! {item}" for item in warnings))
    lines += ["", "清单：", *[f"  {file.relative_to(ROOT).as_posix()}" for file in files]]
    (DIST / "manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def package() -> Path:
    version = read_version()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    DIST.mkdir(exist_ok=True)
    archive = DIST / f"fastcms-{version}-{stamp}.zip"
    files = collect()
    if not files:
        fail("发布包内容为空，请检查源码目录是否完整")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, f"fastcms-{version}/{path.relative_to(ROOT).as_posix()}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    write_manifest(files, version, archive, digest)
    log(f"发布包已生成：{archive}")
    log(f"SHA256：{digest}")
    log(f"清单：{DIST / 'manifest.txt'}")
    warnings = config_warnings()
    if warnings:
        log("发布配置提醒：")
        for item in warnings:
            print(f"  ! {item}")
    return archive


def main(argv: list[str] | None = None) -> int:
    env_check.setup_console()
    parser = argparse.ArgumentParser(description="Fast CMS 发布前检查与打包")
    parser.add_argument("action", nargs="?", choices=("check", "package", "all"), default="all")
    parser.add_argument("--format", choices=("zip", "tar.gz"), default="zip")
    args = parser.parse_args(argv)

    if args.action in {"check", "all"}:
        run_gate()
    if args.action in {"package", "all"}:
        archive = package()
        if args.format == "tar.gz":
            version = read_version()
            target = archive.with_suffix(".tar.gz")
            with tempfile.TemporaryDirectory() as workdir:
                stage = Path(workdir) / f"fastcms-{version}"
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(stage.parent)
                with tarfile.open(target, "w:gz") as tar:
                    tar.add(stage, arcname=f"fastcms-{version}")
            archive.unlink()
            log(f"已转为 tar.gz：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
