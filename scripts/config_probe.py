"""脚本侧配置解析的唯一入口。

后端运行时的配置由 ``app.core.config.Settings`` 决定（环境变量 + 仓库根 ``.env``）。
但 ``scripts/env_check.py`` 要在**后端依赖还没装**的时候就能跑（它的职责就是检查缺什么），
所以不能直接 import ``Settings``。历史上归一化逻辑各写一套（``config.py``、``env_check.py``、
``start.py``），改一处忘一处就漂移——P0-4 的目标就是收敛到这一个文件。

原则：
1. 后端依赖可用时一律走 ``Settings``（权威，含校验与冲突检测）；
2. 依赖不可用时回退到本文件的 ``resolve_*``，其规则与 ``config.py`` 的校验器逐条一致；
3. ``--self-test`` 在两者都可用时逐项比对，任何不一致都让 CI 变红。

用法：
    python scripts/config_probe.py                打印解析后的关键配置
    python scripts/config_probe.py --self-test    比对回退实现与 Settings

脚本内使用：
    import config_probe as cp
    cp.admin_path()   # -> /admin/（带尾斜杠，可直接拼 URL）
"""

from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

DEFAULT_API_PREFIX = "/api/v1"
DEFAULT_ADMIN_BASE = "/admin"
DEFAULT_PORTAL_BASE = "/front"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_DATA_DIR = BACKEND / "data"
DEFAULT_DB_FILENAME = "fastcms.db"
DEFAULT_UPLOAD_SUBDIR = "uploads"

# Settings 字段名 -> .env 键名（仅用于 --self-test 与 --list-env 的对照）
ENV_KEYS = {
    "api_prefix": "API_PREFIX",
    "admin_base": "ADMIN_BASE",
    "portal_base": "PORTAL_BASE",
    "data_dir": "DATA_DIR",
    "db_path": "DB_PATH",
    "db_url": "DB_URL",
    "storage_local_dir": "STORAGE_LOCAL_DIR",
}


def read_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """env 为 None 时读仓库根 .env；传 dict 时原样使用（便于测试隔离）。"""
    if env is not None:
        return env
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# --------------------------------------------------------------------------
# 回退实现：与 backend/app/core/config.py 的校验器逐条一致。
# 只有 Settings 不可用（依赖未装）时才走到这里；可用时以 Settings 为准。
# --------------------------------------------------------------------------
def resolve_api_prefix(env: dict[str, str] | None = None) -> str:
    """对应 Settings._normalize_api_prefix：不带尾斜杠。"""
    prefix = read_env(env).get("API_PREFIX", "").strip().rstrip("/")
    if prefix and not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix or DEFAULT_API_PREFIX


def resolve_site_base(env_key: str, default: str, env: dict[str, str] | None = None) -> str:
    """对应 Settings._normalize_site_base：不带尾斜杠，'/' 表示该站点占根路径。"""
    base = (read_env(env).get(env_key, "").strip() or default).rstrip("/")
    if base and not base.startswith("/"):
        base = f"/{base}"
    return base or "/"


def resolve_host(env: dict[str, str] | None = None) -> str:
    return read_env(env).get("APP_HOST", "") or DEFAULT_HOST


def resolve_site_path(env_key: str, default: str, env: dict[str, str] | None = None) -> str:
    """挂载路径的「可直接拼 URL」形态：/admin/ 或 /。"""
    base = resolve_site_base(env_key, default, env)
    return base if base == "/" else f"{base}/"


def resolve_port(env: dict[str, str] | None = None) -> int:
    raw = read_env(env).get("APP_PORT", "")
    try:
        return int(raw) if raw else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT


def _resolve_relative(raw: str, base: Path) -> str:
    """与 backend/app/core/config.py 的 _resolve_relative 同规则：相对路径以 base 为基准。"""
    path = Path(raw)
    return path.as_posix() if path.is_absolute() else (base / path).as_posix()


def resolve_data_dir(env: dict[str, str] | None = None) -> str:
    """对应 Settings._normalize_data_dir：数据根目录，绝对路径。"""
    raw = read_env(env).get("DATA_DIR", "").strip()
    return _resolve_relative(raw, ROOT) if raw else DEFAULT_DATA_DIR.as_posix()


def resolve_db_url(env: dict[str, str] | None = None) -> str:
    """对应 Settings._resolve_data_defaults：DB_URL > DB_PATH > <数据根>/fastcms.db。"""
    values = read_env(env)
    if values.get("DB_URL", "").strip():
        return values["DB_URL"].strip()
    root = Path(resolve_data_dir(env))
    target = values.get("DB_PATH", "").strip()
    path = Path(target) if target and Path(target).is_absolute() else root / (target or DEFAULT_DB_FILENAME)
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def resolve_storage_dir(env: dict[str, str] | None = None) -> str:
    """对应 Settings._resolve_data_defaults：STORAGE_LOCAL_DIR 留空则 <数据根>/uploads。"""
    values = read_env(env)
    raw = values.get("STORAGE_LOCAL_DIR", "").strip()
    if raw:
        return _resolve_relative(raw, Path(resolve_data_dir(env)))
    return (Path(resolve_data_dir(env)) / DEFAULT_UPLOAD_SUBDIR).as_posix()


# --------------------------------------------------------------------------
# 优先使用 Settings 的封装：依赖可用时返回真实 Settings，否则 None。
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _settings() -> object | None:
    try:
        sys.path.insert(0, str(BACKEND))
        from app.core.config import Settings  # type: ignore[import-not-found]

        return Settings()
    except Exception:
        return None


def settings() -> object | None:
    return _settings()


def using_settings() -> bool:
    return _settings() is not None


def _site_path(fallback: str) -> str:
    """挂载路径统一成可直接拼 URL 的形态：/admin/ 或 /。"""
    return fallback if fallback == "/" else f"{fallback}/"


def admin_path(env: dict[str, str] | None = None) -> str:
    s = _settings()
    if s is not None:
        return _site_path(s.admin_base)  # type: ignore[attr-defined]
    return _site_path(resolve_site_base("ADMIN_BASE", DEFAULT_ADMIN_BASE, env))


def portal_path(env: dict[str, str] | None = None) -> str:
    s = _settings()
    if s is not None:
        return _site_path(s.portal_base)  # type: ignore[attr-defined]
    return _site_path(resolve_site_base("PORTAL_BASE", DEFAULT_PORTAL_BASE, env))


def api_prefix(env: dict[str, str] | None = None) -> str:
    s = _settings()
    if s is not None:
        return s.api_prefix  # type: ignore[attr-defined]
    return resolve_api_prefix(env)


def data_root(env: dict[str, str] | None = None) -> Path:
    """运行时数据根目录：库、上传、密钥、迁移备份、启动锁都跟随它。"""
    s = _settings()
    if s is not None:
        return Path(s.data_root)  # type: ignore[attr-defined]
    return Path(resolve_data_dir(env))


def db_url(env: dict[str, str] | None = None) -> str:
    s = _settings()
    if s is not None:
        return s.db_url  # type: ignore[attr-defined]
    return resolve_db_url(env)


def storage_dir(env: dict[str, str] | None = None) -> str:
    s = _settings()
    if s is not None:
        return s.storage_local_dir  # type: ignore[attr-defined]
    return resolve_storage_dir(env)


def host_port(env: dict[str, str] | None = None) -> tuple[str, int]:
    s = _settings()
    if s is not None:
        return s.app_host, s.app_port  # type: ignore[attr-defined]
    return resolve_host(env), resolve_port(env)


def admin_url(env: dict[str, str] | None = None) -> str:
    host, port = host_port(env)
    return f"http://{host}:{port}{admin_path(env)}"


def portal_url(env: dict[str, str] | None = None) -> str:
    host, port = host_port(env)
    return f"http://{host}:{port}{portal_path(env)}"


# --------------------------------------------------------------------------
# 自检：Settings 与回退实现在各种刁钻输入上必须给出同一答案。
# --------------------------------------------------------------------------
_SELF_TEST_CASES: dict[str, dict[str, str]] = {
    "ADMIN_BASE=admin（无斜杠）": {"ADMIN_BASE": "admin"},
    "ADMIN_BASE=/admin/（尾斜杠）": {"ADMIN_BASE": "/admin/"},
    "ADMIN_BASE 留空": {"ADMIN_BASE": ""},
    "ADMIN_BASE=/（占根路径）": {"ADMIN_BASE": "/"},
    "PORTAL_BASE=front（无斜杠）": {"PORTAL_BASE": "front"},
    "API_PREFIX=api/v1（无斜杠）": {"API_PREFIX": "api/v1"},
    "API_PREFIX=/api/v2/（尾斜杠）": {"API_PREFIX": "/api/v2/"},
    "API_PREFIX 留空": {"API_PREFIX": ""},
    "DATA_DIR 绝对路径": {"DATA_DIR": str(DEFAULT_DATA_DIR.parent.parent / "data-elsewhere")},
    "DATA_DIR 相对路径": {"DATA_DIR": "runtime/data"},
    "DB_PATH 相对路径": {"DB_PATH": "custom/app.db"},
    "DB_PATH 绝对路径": {"DB_PATH": str(ROOT / "custom" / "app.db")},
    "DB_URL 优先于 DB_PATH": {"DB_URL": "mysql+aiomysql://u:p@127.0.0.1:3306/cms", "DB_PATH": "ignored.db"},
    "DB_URL 与 DB_PATH 同设（SQLite）": {"DB_URL": "sqlite+aiosqlite:///x.db", "DB_PATH": "y.db"},
    "STORAGE_LOCAL_DIR 相对路径": {"STORAGE_LOCAL_DIR": "files"},
    "STORAGE_LOCAL_DIR 绝对路径": {"STORAGE_LOCAL_DIR": str(ROOT / "storage")},
    "DATA_DIR 与 DB_PATH 同时设置": {"DATA_DIR": "runtime/data", "DB_PATH": "my.db"},
}


def self_test() -> list[str] | None:
    """逐项比对 Settings 字段与回退实现。

    返回 ``None`` 表示无法比对（后端依赖未装，属跳过而非失败）；
    返回列表表示比对结果，空列表为全部一致。
    """
    if not using_settings():
        return None

    try:
        sys.path.insert(0, str(BACKEND))
        from app.core.config import Settings  # type: ignore[import-not-found]
    except Exception as exc:
        return [f"无法导入 Settings（依赖已装但导入失败）：{type(exc).__name__}: {exc}"]

    fallbacks = {
        "api_prefix": lambda env: resolve_api_prefix(env),
        "admin_base": lambda env: resolve_site_base("ADMIN_BASE", DEFAULT_ADMIN_BASE, env),
        "portal_base": lambda env: resolve_site_base("PORTAL_BASE", DEFAULT_PORTAL_BASE, env),
        "data_dir": lambda env: resolve_data_dir(env),
        "db_url": lambda env: resolve_db_url(env),
        "storage_local_dir": lambda env: resolve_storage_dir(env),
    }
    failures: list[str] = []
    for name, env in _SELF_TEST_CASES.items():
        # 三个字段全部显式传入（空串走各自的「或默认」分支），避免 Settings 去读 .env 干扰
        kwargs = {field: env.get(key, "") for field, key in ENV_KEYS.items()}
        try:
            loaded = Settings(**kwargs)
        except Exception as exc:
            failures.append(f"{name}：Settings 拒绝了该输入（{type(exc).__name__}），回退实现却不会")
            continue
        for field, fallback in fallbacks.items():
            got, want = getattr(loaded, field), fallback(env)
            if got != want:
                failures.append(f"{name}：Settings.{field}={got!r}，回退实现={want!r}")
    return failures


def render() -> str:
    host, port = host_port(None)
    source = "Settings（权威）" if using_settings() else "回退实现（后端依赖未装）"
    return "\n".join(
        [
            "配置解析（单一事实源）",
            f"  来源：{source}",
            f"  APP_HOST={host}  APP_PORT={port}",
            f"  API_PREFIX={api_prefix(None)}",
            f"  ADMIN_BASE={admin_path(None)}",
            f"  PORTAL_BASE={portal_path(None)}",
            f"  后台地址 {admin_url(None)}",
            f"  前台地址 {portal_url(None)}",
            f"  数据根目录 {data_root(None)}",
            f"  数据库     {db_url(None)}",
            f"  上传目录   {storage_dir(None)}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="脚本侧配置解析唯一入口")
    parser.add_argument("--self-test", action="store_true", help="比对回退实现与 Settings 是否一致")
    args = parser.parse_args(argv)

    if args.self_test:
        result = self_test()
        if result is None:
            print("跳过：后端依赖未装，Settings 不可用，无法比对回退实现")
            print("  在 CI 或装好依赖的环境里运行即可比对（python scripts/config_probe.py --self-test）")
            return 0
        if result:
            print("回退实现与 Settings 不一致：")
            for item in result:
                print(f"  - {item}")
            return 1
        print("回退实现与 Settings 完全一致（" f"{len(_SELF_TEST_CASES)} 组输入 × {len(ENV_KEYS)} 个字段）")
        return 0

    print(render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
