#!/usr/bin/env python3
"""部署环境探测：Python、Miniconda/Anaconda、数据库与端口占用。

前端页面由 FastAPI 用 Jinja2 直接渲染，浏览器只加载同源的 CSS 与原生 ES Module，
没有 Node、没有包管理器、没有构建产物，因此这里也不再探测前端工具链。

供 install.py / start.py 复用，也可以单独运行：
    python scripts/env_check.py            可读报告
    python scripts/env_check.py --json     机器可读结果（退出码 0=就绪，1=缺项）
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WEB = BACKEND / "app" / "web"
VENV = BACKEND / ".venv"
IS_WINDOWS = os.name == "nt"

# 归一化规则统一收敛在 config_probe，这里不再各写一套
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config_probe  # noqa: E402

MIN_PYTHON = (3, 12)

# 运行时必须能 import 的顶层模块。缺任何一个，要么启动即失败，要么首次用到该功能时炸：
# 例如缺 python-multipart 时，带 File() 的路由在导入阶段就报错；缺 argon2 时登录直接不可用。
# 这份列表必须与 backend/requirements.txt 对齐，只探「能启动的最小子集」会漏掉一半问题。
RUNTIME_MODULES: tuple[str, ...] = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "alembic",
    "pydantic",
    "pydantic_settings",
    "aiosqlite",
    "apscheduler",
    "jwt",           # PyJWT
    "argon2",        # argon2-cffi
    "psutil",
    "jinja2",        # Jinja2
    "filetype",
    "multipart",     # python-multipart
    "httpx",
    "ip2region",     # py-ip2region
)
# 可选能力：代码里是惰性导入，未启用对应开关时缺失不影响启动
OPTIONAL_MODULES: dict[str, str] = {
    "aiomysql": "DB_URL 使用 mysql+aiomysql:// 时需要",
    "redis": "REDIS_ENABLED=true 时需要",
}
# 纯数据包：没有可 import 的模块名，只能查发行版元数据
RUNTIME_DISTRIBUTIONS: tuple[str, ...] = ("tzdata",)
# 兼容旧调用方：一次 import 全部必需模块
BACKEND_PROBE = ", ".join(RUNTIME_MODULES)

OK, WARN, MISSING = "ok", "warn", "missing"
# 只有这几项缺失会让安装/启动失败；conda 是可选方案，端口占用可以换端口
BLOCKING_KEYS = frozenset({"python", "web"})


@dataclass
class Finding:
    key: str
    label: str
    status: str
    detail: str
    hint: str = ""


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.strip().lstrip("vV").split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _call(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    """只取 stdout：conda 在 Windows 上会把依赖告警写进 stderr，混进来会污染解析结果。"""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return result.returncode, result.stdout or ""


def probe_dependencies(python: Path | str) -> list[str]:
    """返回目标解释器缺失的运行时依赖；空列表表示齐全。

    逐个 import 而不是一把梭，是为了能报出「到底缺哪几个」——只报一句
    「依赖不全」，用户还得自己猜。纯数据包（tzdata）没有模块名，改查发行版元数据。
    """
    modules = ", ".join(repr(name) for name in RUNTIME_MODULES)
    distributions = ", ".join(repr(name) for name in RUNTIME_DISTRIBUTIONS)
    snippet = "\n".join(
        (
            "import importlib, importlib.metadata",
            "missing = []",
            f"for name in ({modules},):",
            "    try:",
            "        importlib.import_module(name)",
            "    except Exception:",
            "        missing.append(name)",
            f"for dist in ({distributions},):",
            "    try:",
            "        importlib.metadata.version(dist)",
            "    except Exception:",
            "        missing.append(dist)",
            "print(' '.join(missing))",
        )
    )
    code, output = _call([str(python), "-c", snippet], timeout=60)
    if code != 0:
        # 解释器本身跑不起来，交给调用方按「依赖全缺」处理
        return ["<interpreter>"]
    return output.split()


def read_root_env() -> dict[str, str]:
    path = ROOT / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# 与 app/core/config.py 的 LOCAL_ENVS 保持一致：这些环境按明文 HTTP 处理，不下发 Secure
LOCAL_ENVS = frozenset({"dev", "development", "local", "test", "pytest"})


def cookie_secure_effective(env: dict[str, str] | None = None) -> bool:
    """刷新令牌 Cookie 是否带 Secure —— 与后端 Settings.cookie_secure 同规则。

    Secure=true 而用 http:// 访问时，浏览器根本不保存 Cookie，表现为「登录成功、
    十几分钟后突然掉线」，是内网部署最容易踩的坑，所以启动前要能判断出来。
    """
    values = read_root_env() if env is None else env
    raw = (values.get("AUTH_COOKIE_SECURE") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    app_env = (values.get("APP_ENV") or "dev").strip().lower()
    return app_env not in LOCAL_ENVS


def local_ipv4_addresses() -> list[str]:
    """本机对外 IPv4 地址，用来把「别人该访问哪个地址」直接打出来。"""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(str(info[4][0]))
    except OSError:
        pass
    # UDP connect 不发包，只让内核选一条默认出口路由，用来兜住 hostname 解析不到的情况
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(sock.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in addresses if not ip.startswith("127."))


def api_prefix(env: dict[str, str] | None = None) -> str:
    """按后端 Settings.api_prefix 的规则归一化，脚本拼接口地址时必须与后端一致。

    归一化规则统一收敛在 scripts/config_probe.py，这里只做转发。
    显式传入 env（如 read_root_env()）时用它；否则用 Settings（读 .env + 环境变量）。
    """
    if env is None:
        return config_probe.api_prefix()
    return config_probe.resolve_api_prefix(env)


def admin_base(env: dict[str, str] | None = None) -> str:
    """后台挂载路径，取自 ADMIN_BASE；返回带尾斜杠形态，可直接拼 URL。"""
    if env is None:
        return config_probe.admin_path()
    return config_probe.resolve_site_path("ADMIN_BASE", config_probe.DEFAULT_ADMIN_BASE, env)


def portal_base(env: dict[str, str] | None = None) -> str:
    """前台挂载路径，取自 PORTAL_BASE；返回带尾斜杠形态，可直接拼 URL。"""
    if env is None:
        return config_probe.portal_path()
    return config_probe.resolve_site_path("PORTAL_BASE", config_probe.DEFAULT_PORTAL_BASE, env)


def _conda_kind(prefix: Path) -> str:
    name = prefix.name.lower()
    if "anaconda" in name:
        return "Anaconda"
    if "miniforge" in name:
        return "Miniforge"
    if "miniconda" in name:
        return "Miniconda"
    return "Conda"


def python_of(base: Path) -> Path:
    return base / ("python.exe" if IS_WINDOWS else "bin/python")


def conda_candidates() -> list[Path]:
    """返回所有可能的 conda 根目录（含 Miniconda/Anaconda/Miniforge）。"""
    roots: list[Path] = []
    home = Path.home()
    if IS_WINDOWS:
        for base in (
            home / "miniconda3",
            home / "anaconda3",
            home / "miniforge3",
            home / "AppData" / "Local" / "miniconda3",
            home / "AppData" / "Local" / "anaconda3",
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "miniconda3",
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "anaconda3",
        ):
            roots.append(base)
        for drive in ("C:\\", "D:\\"):
            for name in ("miniconda3", "anaconda3", "ProgramData/miniconda3", "ProgramData/anaconda3"):
                roots.append(Path(drive) / name)
    else:
        roots += [
            home / "miniconda3",
            home / "anaconda3",
            home / "miniforge3",
            Path("/opt/conda"),
            Path("/opt/miniconda3"),
            Path("/opt/anaconda3"),
            Path("/usr/local/miniconda3"),
            Path("/usr/local/anaconda3"),
            Path("/opt/homebrew/Caskroom/miniconda/base"),
        ]
    env_bases = [os.environ.get("CONDA_PREFIX_1"), os.environ.get("MAMBA_ROOT_PREFIX")]
    for value in env_bases:
        if value:
            roots.append(Path(value))
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def find_conda() -> dict[str, object]:
    """探测 conda 可执行文件、安装类型，以及其中可用的 Python 3.12+ 环境。"""
    exe = os.environ.get("CONDA_EXE") or shutil.which("conda") or shutil.which("mamba")
    if exe is None:
        for root in conda_candidates():
            candidate = root / ("Scripts/conda.exe" if IS_WINDOWS else "bin/conda")
            if candidate.exists():
                exe = str(candidate)
                break
    info: dict[str, object] = {"exe": exe, "base": None, "flavour": None, "envs": []}
    if exe is None:
        return info

    code, output = _call([exe, "info", "--base"])
    base = Path(output.strip()) if code == 0 and output.strip() else None
    if base is None or not base.exists():
        base = next(
            (item for item in ([Path(str(exe)).parents[1], *conda_candidates()]) if python_of(item).exists()),
            None,
        )
    info["base"] = str(base) if base else None
    info["flavour"] = _conda_kind(base) if base else _conda_kind(Path(exe).parents[1])

    envs: list[dict[str, str]] = []
    code, output = _call([exe, "info", "--envs", "--json"])
    if code == 0:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = {}
        # 新版 conda 返回 env_name_details 映射，老版本只返回 envs 路径列表
        details = payload.get("env_name_details") or {}
        if details:
            envs = [
                {"name": key if key != "~" else "base", "path": item["nameLoc"]}
                for key, item in details.items()
                if isinstance(item, dict) and item.get("nameLoc")
            ]
        else:
            envs = [
                {"name": "base" if str(path) == str(base) else Path(str(path)).name, "path": str(path)}
                for path in (payload.get("envs") or [])
            ]
    else:
        for root in conda_candidates():
            env_dir = root / "envs"
            if env_dir.is_dir():
                envs.extend({"name": item.name, "path": str(item)} for item in env_dir.iterdir() if item.is_dir())
            if python_of(root).exists():
                envs.append({"name": "base", "path": str(root)})
    usable: list[dict[str, str]] = []
    for env in envs:
        python = python_of(Path(env["path"]))
        if not python.exists():
            continue
        code, output = _call([str(python), "-c", "import sys;print('.'.join(map(str, sys.version_info[:3])))"])
        version = output.strip()
        if code == 0 and version_tuple(version) >= MIN_PYTHON:
            usable.append({"name": env["name"], "path": env["path"], "python": version, "executable": str(python)})
    info["envs"] = usable
    return info


def python_finding(conda: dict[str, object]) -> Finding:
    version = ".".join(map(str, sys.version_info[:3]))
    in_venv = sys.prefix != sys.base_prefix
    detail = f"{version}（{sys.executable}）"
    if in_venv:
        detail += "，虚拟环境"
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    if conda_env and conda_env != "base":
        detail += f"，conda 环境 {conda_env}"
    if version_tuple(version) >= MIN_PYTHON:
        return Finding("python", "Python", OK, detail)
    usable = conda.get("envs") or []
    hint = f"需要 Python {'.'.join(map(str, MIN_PYTHON))}+"
    if usable:
        hint += "；检测到可用的 conda 环境，可执行 `python scripts/install.py --use-conda`"
    else:
        hint += "，请安装官方 Python 或通过 Miniconda 创建 3.12 环境"
    return Finding("python", "Python", MISSING, detail, hint=hint)


def backend_finding(python: Path | None = None) -> Finding:
    """检查后端依赖。传 python 时检查该解释器（无 venv 的部署方式），否则检查 backend/.venv。"""
    target = python or VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    where = "当前解释器" if python is not None else "backend/.venv"
    if python is None and not target.exists():
        return Finding(
            "backend",
            "后端依赖",
            WARN,
            "backend/.venv 尚未创建",
            hint="运行 `python scripts/install.py`，或用 `--system` 装进当前环境",
        )
    missing = probe_dependencies(target)
    if missing:
        detail = f"{where} 缺少 {len(missing)} 项：{'、'.join(missing[:6])}"
        if len(missing) > 6:
            detail += " 等"
        return Finding(
            "backend",
            "后端依赖",
            WARN,
            detail,
            hint="运行 `python scripts/install.py` 补齐，或 `pip install -r backend/requirements.txt`",
        )
    _code, output = _call([str(target), "-c", "import sys;print('.'.join(map(str, sys.version_info[:3])))"])
    return Finding("backend", "后端依赖", OK, f"{where}已安装（Python {output.strip()}）")


def optional_dependency_finding(python: Path | None = None) -> Finding:
    """可选依赖只提示不阻断：Redis / MySQL 驱动缺失时对应开关打开才会出问题。"""
    target = python or VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    if not target.exists():
        return Finding("optional", "可选依赖", OK, "未检测（无解释器）")
    modules = ", ".join(repr(name) for name in OPTIONAL_MODULES)
    snippet = "\n".join(
        (
            "import importlib",
            "missing = []",
            f"for name in ({modules},):",
            "    try:",
            "        importlib.import_module(name)",
            "    except Exception:",
            "        missing.append(name)",
            "print(' '.join(missing))",
        )
    )
    code, output = _call([str(target), "-c", snippet], timeout=30)
    if code != 0:
        return Finding("optional", "可选依赖", WARN, "探测失败")
    missing = output.split()
    if not missing:
        return Finding("optional", "可选依赖", OK, "MySQL 驱动与 Redis 客户端均已安装")
    hints = "；".join(f"{name}：{OPTIONAL_MODULES[name]}" for name in missing)
    return Finding("optional", "可选依赖", WARN, f"未安装 {'、'.join(missing)}（不影响默认形态）", hint=hints)


def web_finding() -> Finding:
    """页面模板与静态资源随源码分发，缺了任何一个后端都会直接 500。"""
    missing = [
        name
        for name in (
            "templates/base.html",
            "templates/components/ui.html",
            "templates/layouts/admin.html",
            "templates/layouts/portal.html",
            "templates/layouts/blank.html",
            "static/css/base.css",
            "static/css/admin.css",
            "static/css/portal.css",
            "static/css/public.css",
            "static/js/app.js",
            "static/js/shell/admin.js",
        )
        if not (WEB / name).exists()
    ]
    if missing:
        return Finding("web", "HTML 页面资源", MISSING, f"缺少 {'、'.join(missing)}", hint="源码包不完整，请重新获取")
    return Finding("web", "HTML 页面资源", OK, "模板、组件与分层样式就绪（无需构建）")


def database_finding(env: dict[str, str]) -> Finding:
    # 默认库路径不再在脚本里各写一份：统一走 config_probe，
    # 这样 DATA_DIR / DB_PATH 的改动在探测和运行时看到的是同一个结果
    url = config_probe.resolve_db_url(env)
    if url.startswith("sqlite"):
        return Finding("database", "数据库", OK, "SQLite（无需额外服务）")
    label = url.split("://", 1)[0]
    hint = "确认数据库服务已启动且账号库名正确"
    if label.startswith("mysql"):
        hint = "MySQL 需已启动并建好库；无需数据库时可把 DB_URL 留空改用 SQLite"
    return Finding("database", "数据库", WARN, f"{label}（{url.split('://', 1)[-1][:48]}）", hint=hint)


def redis_finding(env: dict[str, str]) -> Finding:
    if env.get("REDIS_ENABLED", "").lower() in {"1", "true", "yes"}:
        return Finding(
            "redis", "Redis", WARN, f"已启用 {env.get('REDIS_URL', '')}", hint="确认 Redis 可达；单机部署可设为 false 使用进程内缓存"
        )
    return Finding("redis", "Redis", OK, "未启用，使用进程内缓存")


def _env_or_dotenv(key: str, env: dict[str, str]) -> str:
    """真实环境变量优先，其次 .env（与 pydantic-settings 的优先级一致）。"""
    return os.environ.get(key, env.get(key, "")).strip()


def combo_finding(env: dict[str, str]) -> Finding:
    """组合校验（3-3）：workers × redis × scheduler 的跨进程一致性。

    - 多 worker + 无 Redis：进程内缓存各持一份，字典/参数等全局缓存跨进程不一致；
    - 多 worker + embedded 调度器：每个 worker 各自持有调度器，任务重复执行、
      next_run_at 抖动（A-9，默认已被后端启动拒绝，此处给出更早的提示）。
    """
    try:
        workers = int(_env_or_dotenv("APP_WORKERS", env) or "1")
    except ValueError:
        workers = 1
    redis_on = _env_or_dotenv("REDIS_ENABLED", env).lower() in {"1", "true", "yes", "on"}
    scheduler_mode = (_env_or_dotenv("SCHEDULER_MODE", env) or "embedded").lower()

    if workers <= 1:
        return Finding("combo", "部署组合", OK, "单进程形态（多 worker 需要 Redis/独立调度器）")
    issues: list[str] = []
    if not redis_on:
        issues.append("未启用 Redis，字典/参数等进程内缓存会在多 worker 间不一致")
    if scheduler_mode == "embedded":
        issues.append("embedded 调度器会被每个 worker 各自持有，任务可能重复执行（建议 standalone）")
    if not issues:
        return Finding("combo", "部署组合", OK, f"{workers} worker × Redis 已启用 × 调度器 {scheduler_mode}")
    return Finding(
        "combo",
        "部署组合",
        WARN,
        f"{workers} worker：{'；'.join(issues)}",
        hint="多进程写库请启用 Redis 并把 SCHEDULER_MODE 设为 standalone 或 disabled",
    )


def port_finding(env: dict[str, str]) -> Finding:
    """前后台页面与接口共用同一个端口，只需要探一个。"""
    host = env.get("APP_HOST", "127.0.0.1") or "127.0.0.1"
    try:
        port = int(env.get("APP_PORT", "8000") or "8000")
    except ValueError:
        port = 8000
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        busy = sock.connect_ex((host, port)) == 0
    return Finding(
        f"port_{port}",
        f"服务端口 {port}",
        WARN if busy else OK,
        "已被占用" if busy else "空闲",
        hint="启动器会自动改选空闲端口，也可用 `--backend-port` 指定" if busy else "",
    )


def conda_finding(conda: dict[str, object]) -> Finding:
    exe = conda.get("exe")
    if not exe:
        return Finding(
            "conda",
            "Miniconda/Anaconda",
            WARN,
            "未检测到 conda（可选）",
            hint="需要独立环境时可安装 Miniconda 后执行 `python scripts/install.py --use-conda`",
        )
    envs = conda.get("envs") or []
    flavour = conda.get("flavour") or "Conda"
    base = conda.get("base") or Path(str(exe)).parents[1]
    detail = f"{flavour}：{base}"
    if envs:
        detail += "；可用环境 " + "、".join(f"{item['name']}({item['python']})" for item in envs)
    return Finding("conda", "Miniconda/Anaconda", OK, detail)


def build_report() -> dict[str, object]:
    env = read_root_env()
    conda = find_conda()
    usable_envs = conda.get("envs") or []
    findings: list[Finding] = [
        python_finding(conda),
        conda_finding(conda),
        backend_finding(),
        optional_dependency_finding(),
        web_finding(),
        database_finding(env),
        redis_finding(env),
        combo_finding(env),
        port_finding(env),
    ]
    blocking = [item.key for item in findings if item.status == MISSING and item.key in BLOCKING_KEYS]
    return {
        "ready": not blocking,
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "workspace": str(ROOT),
        "conda_python_envs": usable_envs,
        "items": [asdict(item) for item in findings],
        "blocking": blocking,
    }


def render(report: dict[str, object]) -> str:
    marks = {OK: "✓", WARN: "!", MISSING: "✗"}
    lines = [f"环境探测 · {report['os']} · {report['workspace']}"]
    for item in report["items"]:  # type: ignore[index]
        suffix = f" — {item['hint']}" if item["hint"] else ""
        lines.append(f"  [{marks[item['status']]:^1}] {item['label']}: {item['detail']}{suffix}")
    lines.append("结论：" + ("环境就绪" if report["ready"] else f"缺少 {'、'.join(report['blocking'])}，请先按提示安装"))  # type: ignore[arg-type]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast CMS 部署环境探测")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于脚本消费")
    args = parser.parse_args(argv)
    setup_console()
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(render(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
