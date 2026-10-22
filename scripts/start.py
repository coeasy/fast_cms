#!/usr/bin/env python3
"""Fast CMS 本地一键启动器，支持 Windows / Linux / macOS。

后台与前台页面都由 FastAPI 用 Jinja2 直接渲染，与 API 同源同端口，
因此只有「后端」一个进程需要拉起，不存在单独的前端服务。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV = BACKEND / ".venv"
LOG_DIR = BACKEND / "data" / "logs"
TEST_DB = BACKEND / "data" / "quickstart-test.db"
TEST_UPLOADS = BACKEND / "data" / "uploads-test"
DEFAULT_TEST_PASSWORD = "fast_cms_123"
IS_WINDOWS = os.name == "nt"
PRINT_LOCK = threading.Lock()


class LauncherError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[start] {message}", flush=True)


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


DEFAULT_API_PREFIX = "/api/v1"
DEFAULT_ADMIN_BASE = "/admin"
DEFAULT_PORTAL_BASE = "/front"


def read_root_env() -> dict[str, str]:
    """解析根目录 .env，供启动脚本自身使用；后端进程由 pydantic-settings 读取同一文件。"""
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


def resolve_api_prefix(env: dict[str, str]) -> str:
    """与后端 Settings.api_prefix 保持一致的归一化规则。"""
    raw = env.get("API_PREFIX") or read_root_env().get("API_PREFIX") or DEFAULT_API_PREFIX
    prefix = raw.strip().rstrip("/")
    if not prefix.startswith("/"):
        prefix = f"/{prefix}" if prefix else ""
    return prefix or DEFAULT_API_PREFIX


def resolve_admin_base(env: dict[str, str]) -> str:
    """后台挂载路径，与后端 Settings.admin_base 同规则；返回带尾斜杠形态供拼 URL 使用。"""
    return resolve_site_base("ADMIN_BASE", DEFAULT_ADMIN_BASE, env)


def resolve_portal_base(env: dict[str, str]) -> str:
    """前台挂载路径，与后端 Settings.portal_base 同规则；返回带尾斜杠形态供拼 URL 使用。"""
    return resolve_site_base("PORTAL_BASE", DEFAULT_PORTAL_BASE, env)


def resolve_site_base(key: str, default: str, env: dict[str, str]) -> str:
    raw = env.get(key) or read_root_env().get(key) or default
    base = (raw.strip() or default).rstrip("/")
    if base and not base.startswith("/"):
        base = f"/{base}"
    return f"{base}/" if base else "/"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def spawn_backend(python: Path, host: str, port: int, reload: bool, env: dict[str, str]) -> subprocess.Popen[str]:
    command = [
        str(python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        command.append("--reload")
    kwargs: dict[str, object] = {
        "cwd": BACKEND,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def start_pump(proc: subprocess.Popen[str], label: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def pump() -> None:
        assert proc.stdout is not None
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                for line in proc.stdout:
                    log_file.write(line)
                    log_file.flush()
                    with PRINT_LOCK:
                        print(f"[{label}] {line.rstrip()}", flush=True)
        except (OSError, ValueError):
            pass

    threading.Thread(target=pump, name=f"pump-{label}", daemon=True).start()


def terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_port(explicit: int | None, preferred: int, used: set[int], label: str) -> int:
    if explicit is not None:
        if not 1 <= explicit <= 65535:
            raise LauncherError(f"{label}端口 {explicit} 不合法，应在 1-65535 之间")
        if explicit in used or not port_available(explicit):
            raise LauncherError(f"{label}端口 {explicit} 已被占用，请换一个端口")
        used.add(explicit)
        return explicit
    for port in range(preferred, min(preferred + 100, 65536)):
        if port not in used and port_available(port):
            used.add(port)
            return port
    raise LauncherError(f"找不到可用的{label}端口")


def ensure_env_file() -> None:
    env_file = ROOT / ".env"
    example = ROOT / ".env.example"
    if not env_file.exists() and example.exists():
        shutil.copy2(example, env_file)
        log("已创建 .env（来自 .env.example）")


def ensure_backend(env: dict[str, str]) -> Path:
    python = venv_python()
    if not python.exists():
        log("未找到 backend/.venv，正在创建虚拟环境...")
        result = subprocess.run([sys.executable, "-m", "venv", str(VENV)], cwd=ROOT, env=env, check=False)
        if result.returncode != 0 or not python.exists():
            raise LauncherError("创建后端虚拟环境失败，请确认 Python 3.12+ 可用")
    probe = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn, sqlalchemy, aiosqlite, jinja2"],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        log("安装后端依赖...")
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "-e", "backend[dev]"],
            cwd=ROOT,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise LauncherError("后端依赖安装失败")
    return python


def init_database(python: Path, env: dict[str, str]) -> None:
    log("初始化/校验数据库...")
    result = subprocess.run([str(python), "-m", "app.cli", "init"], cwd=BACKEND, env=env, check=False)
    if result.returncode != 0:
        raise LauncherError("数据库初始化失败，请查看上方输出")


def ensure_demo_table() -> None:
    """测试库补一张业务表：全部内置表都是 sys_ 前缀，代码生成链路没有可用目标。"""
    if not TEST_DB.exists():
        return
    with sqlite3.connect(TEST_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS demo_article ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title VARCHAR(120) NOT NULL, "
            "content TEXT, "
            "views INTEGER DEFAULT 0, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.commit()


def prepare_test_env(keep_db: bool) -> tuple[dict[str, str], str | None]:
    exists = TEST_DB.exists()
    reset = not keep_db or not exists
    if reset:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{TEST_DB}{suffix}").unlink(missing_ok=True)
        shutil.rmtree(TEST_UPLOADS, ignore_errors=True)
    password = os.environ.get("FASTCMS_TEST_ADMIN_PASSWORD", DEFAULT_TEST_PASSWORD)
    env = base_env()
    env.update(
        {
            "DB_URL": f"sqlite+aiosqlite:///{TEST_DB.as_posix()}",
            "APP_ENV": "dev",
            "INITIAL_ADMIN_PASSWORD": password,
            "AUTH_COOKIE_SECURE": "false",
            "REDIS_ENABLED": "false",
            "SCHEDULER_MODE": "embedded",
            "AUTO_MIGRATE": "false",
            "STORAGE_LOCAL_DIR": str(TEST_UPLOADS),
        }
    )
    return env, password if reset else None


def wait_for_http(url: str, label: str, timeout: int, procs: list[tuple[str, subprocess.Popen[str]]]) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for name, proc in procs:
            code = proc.poll()
            if code is not None:
                raise LauncherError(f"{name} 进程提前退出（退出码 {code}），请查看上方日志")
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(0.4)
    raise LauncherError(f"{label}启动超时（{timeout}s）：{url}")


def check_login(backend_url: str, password: str, api_prefix: str = DEFAULT_API_PREFIX) -> None:
    request = urllib.request.Request(
        f"{backend_url}{api_prefix}/auth/login",
        data=json.dumps({"username": "admin", "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise LauncherError(f"管理员登录自检失败：HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise LauncherError(f"管理员登录自检失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise LauncherError(f"管理员登录自检失败：{exc}") from exc


def print_ready(
    mode: str,
    backend_url: str,
    web_url: str,
    portal_url: str | None,
    password: str | None,
    log_dir: Path,
) -> None:
    print("=" * 64)
    print(f"  模式：{mode}")
    print(f"  后端 API：{backend_url}")
    print(f"  后台页面：{web_url}")
    if portal_url:
        print(f"  前台站点：{portal_url}")
    if password:
        print(f"  管理员：admin / {password}")
        print("  提示：本地测试库可直接登录；生产环境（APP_ENV=production）首次登录会强制改密")
    elif mode == "test":
        print("  管理员：测试库已保留，密码沿用上次初始化")
    else:
        print("  管理员：首次初始化密码见上方输出；已有库沿用原密码")
    print(f"  接口文档：{backend_url}/docs")
    print(f"  日志目录：{log_dir}")
    print("  按 Ctrl+C 停止")
    print("=" * 64)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fast CMS 本地一键启动（默认 SQLite，不依赖 Docker / MySQL / Redis）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python scripts/start.py                 开发模式，单进程 + 热重载（自动选择端口）
  python scripts/start.py preview         按 .env 的 APP_HOST/APP_PORT 启动，关闭热重载
  python scripts/start.py test            独立测试库 + 固定测试密码
  python scripts/start.py test --check    启动自检，就绪后自动退出
""",
    )
    parser.add_argument("mode", nargs="?", choices=("dev", "preview", "test"), default="dev")
    parser.add_argument("--host", default=None, help="监听地址，默认开发/测试用 127.0.0.1，预览取 .env 的 APP_HOST")
    parser.add_argument("--backend-port", "--port", dest="backend_port", type=int, default=None, help="服务端口")
    parser.add_argument("--keep-db", action="store_true", help="测试模式保留已有测试库")
    parser.add_argument("--open", action="store_true", help="就绪后打开浏览器")
    parser.add_argument("--check", action="store_true", help="就绪后自检并退出")
    return parser.parse_args(argv)


def resolve_bind_host(explicit: str | None, env: dict[str, str], mode: str) -> str:
    if explicit:
        return explicit
    if mode != "preview":
        # 开发模式固定只监听回环，避免把带热重载的未加固服务暴露到局域网
        return "127.0.0.1"
    return read_root_env().get("APP_HOST") or env.get("APP_HOST") or "127.0.0.1"


def resolve_bind_port(explicit: int | None, env: dict[str, str], mode: str) -> int | None:
    if explicit is not None:
        return explicit
    if mode != "preview":
        return None
    raw = read_root_env().get("APP_PORT") or env.get("APP_PORT") or ""
    try:
        return int(raw) if raw else None
    except ValueError:
        raise LauncherError(f".env 里的 APP_PORT={raw!r} 不是合法端口号") from None


def main(argv: list[str] | None = None) -> int:
    setup_console()
    # 启动脚本可能在低于项目要求的 Python 上运行，需要自己给出提示
    if sys.version_info < (3, 12):  # noqa: UP036
        log(f"启动失败：需要 Python 3.12+，当前为 {sys.version.split()[0]}")
        return 1
    args = parse_args(argv)
    procs: list[tuple[str, subprocess.Popen[str]]] = []
    try:
        ensure_env_file()
        if args.mode == "test":
            env, password = prepare_test_env(args.keep_db)
        else:
            env, password = base_env(), None

        python = ensure_backend(env)
        api_prefix = resolve_api_prefix(env)
        admin_base = resolve_admin_base(env)
        # 拼 URL 用不带尾斜杠的形态，挂在根路径时退化成空串
        admin_path = admin_base.rstrip("/")
        portal_base = resolve_portal_base(env)
        portal_path = portal_base.rstrip("/")

        used: set[int] = set()
        bind_host = resolve_bind_host(args.host, env, args.mode)
        # .env 的 APP_PORT 只作为首选端口（被占用则后移），命令行显式指定时才强制该端口
        preferred_port = resolve_bind_port(None, env, args.mode) or 8000
        backend_port = choose_port(args.backend_port, preferred_port, used, "服务")
        backend_url = f"http://127.0.0.1:{backend_port}"
        # 让后端 Settings 与实际监听保持一致（Cookie Path、日志与告警都读这两个值）
        env["APP_HOST"] = bind_host
        env["APP_PORT"] = str(backend_port)

        init_database(python, env)
        if args.mode == "test":
            ensure_demo_table()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        log(f"启动服务：{backend_url}（监听 {bind_host}:{backend_port}）")
        backend = spawn_backend(python, bind_host, backend_port, reload=args.mode == "dev", env=env)
        procs.append(("api", backend))
        start_pump(backend, "api", LOG_DIR / f"{stamp}-api.log")
        wait_for_http(f"{backend_url}/healthz", "服务", 90, procs)

        # 前后台页面与接口同源同端口，都由这一个进程输出
        web_url = f"{backend_url}{admin_path}"
        print_ready(
            args.mode,
            backend_url,
            web_url,
            f"{backend_url}{portal_path}" if portal_path else backend_url,
            password,
            LOG_DIR,
        )

        if args.check:
            wait_for_http(web_url, "页面", 15, procs)
            if args.mode == "test" and password:
                check_login(backend_url, password, api_prefix)
                log("管理员登录自检通过")
            log("启动自检通过，正在停止...")
            return 0
        if args.open:
            webbrowser.open(web_url)
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    log(f"{name} 进程已退出（退出码 {code}）")
                    return code if code != 0 else 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在停止...")
        return 0
    except LauncherError as exc:
        log(f"启动失败：{exc}")
        return 1
    finally:
        for name, proc in reversed(procs):
            terminate(proc)
        if procs:
            log("已停止")


if __name__ == "__main__":
    sys.exit(main())
