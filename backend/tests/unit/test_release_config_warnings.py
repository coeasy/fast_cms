"""发布配置检查的回归：config_warnings() 曾经只读 .env 文件，环境变量配好了也照样报缺失。

这些用例把「不许再犯」钉死：
- 真实环境变量优先于 .env（与 pydantic-settings 的优先级一致）；
- 留空 = 用默认值，默认值本身就符合发布建议，不许误报（本地零配置启动是设计目标）；
- production 与 prod 等价，只认 prod 会把合法配置教成错；
- 只有多进程 + SQLite 才提示，单进程 SQLite 是默认形态、不是问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import env_check  # noqa: E402
import release  # noqa: E402

WATCHED = set(release.RELEASE_CHECKS) | {"DB_URL", "APP_WORKERS", "REDIS_ENABLED", "SCHEDULER_MODE"}
PROD_SECRETS = {"APP_SECRET_KEY": "x" * 48, "INITIAL_ADMIN_PASSWORD": "Str0ng!Passw0rd"}
MYSQL = "mysql+aiomysql://fastcms:fastcms@127.0.0.1:3306/fastcms?charset=utf8mb4"
ENV_ADVISORY = "APP_ENV=dev，发布建议 prod/production"
MULTI_PROCESS = [
    "APP_WORKERS>1 或调度器独立运行，但未启用 Redis，字典/参数缓存会在各进程间不一致",
    "多进程写库请把 DB_URL 改为 MySQL，SQLite 同文件并发写会 database is locked",
]


def run_warnings(monkeypatch: pytest.MonkeyPatch, dotenv: dict[str, str], env_vars: dict[str, str]) -> list[str]:
    """按给定 .env 内容与真实环境变量跑一次 config_warnings()。"""
    for key in WATCHED:
        monkeypatch.delenv(key, raising=False)
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(env_check, "read_root_env", lambda: dict(dotenv))
    return release.config_warnings()


def test_zero_config_only_reports_env_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    """零配置启动是设计目标：除了 APP_ENV 的提示，不该有任何噪音。"""
    assert run_warnings(monkeypatch, {}, {}) == [ENV_ADVISORY]


def test_real_env_vars_override_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker/systemd 里没人写 .env：环境变量配齐了就必须安静。"""
    dotenv = {"APP_ENV": "dev"}
    env_vars = {"APP_ENV": "production", **PROD_SECRETS, "RATE_LIMIT_ENABLED": "true", "AUTO_MIGRATE": "false", "DB_URL": MYSQL}
    assert run_warnings(monkeypatch, dotenv, env_vars) == []


def test_effective_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_check, "read_root_env", lambda: {"APP_ENV": "dev", "APP_WORKERS": "1"})
    monkeypatch.delenv("APP_ENV", raising=False)
    assert release.effective_env()["APP_ENV"] == "dev"
    monkeypatch.setenv("APP_ENV", "production")
    assert release.effective_env()["APP_ENV"] == "production"


def test_production_missing_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings = run_warnings(monkeypatch, {"APP_ENV": "production"}, {})
    assert sorted(warnings) == [
        "APP_SECRET_KEY 未设置（生产环境必须显式提供）",
        "INITIAL_ADMIN_PASSWORD 未设置（生产环境必须显式提供）",
    ]


def test_production_alias_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """production 与 prod 等价（PRODUCTION_ENVS 两个都认），不许当成违例报。"""
    dotenv = {"APP_ENV": "production", **PROD_SECRETS, "RATE_LIMIT_ENABLED": "true", "AUTO_MIGRATE": "false", "DB_URL": MYSQL}
    assert run_warnings(monkeypatch, dotenv, {}) == []


def test_single_worker_sqlite_is_not_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    """当前 .env 的真实形态：SQLite + 单 worker + 内嵌调度器，只有 APP_ENV 一条提示。"""
    dotenv = {
        "APP_ENV": "dev",
        "APP_WORKERS": "1",
        "SCHEDULER_MODE": "embedded",
        "REDIS_ENABLED": "false",
        "RATE_LIMIT_ENABLED": "true",
        "AUTO_MIGRATE": "false",
    }
    assert run_warnings(monkeypatch, dotenv, {}) == [ENV_ADVISORY]


def test_multi_process_sqlite_without_redis_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = {"APP_ENV": "prod", **PROD_SECRETS, "APP_WORKERS": "4"}
    assert sorted(run_warnings(monkeypatch, dotenv, {})) == sorted(MULTI_PROCESS)


def test_standalone_scheduler_with_redis_and_mysql_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = {
        "APP_ENV": "prod",
        **PROD_SECRETS,
        "SCHEDULER_MODE": "standalone",
        "REDIS_ENABLED": "true",
        "DB_URL": MYSQL,
    }
    assert run_warnings(monkeypatch, dotenv, {}) == []


def test_boolean_spellings_are_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """pydantic-settings 认 1/yes/on/off，字符串直接比较会把合法配置误报成违例。"""
    dotenv = {
        "APP_ENV": "prod",
        **PROD_SECRETS,
        "RATE_LIMIT_ENABLED": "1",
        "AUTO_MIGRATE": "off",
        "DB_URL": MYSQL,
    }
    assert run_warnings(monkeypatch, dotenv, {}) == []


def test_real_boolean_violation_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = {"APP_ENV": "prod", **PROD_SECRETS, "RATE_LIMIT_ENABLED": "no", "DB_URL": MYSQL}
    assert run_warnings(monkeypatch, dotenv, {}) == ["RATE_LIMIT_ENABLED=false，发布建议 true"]


def test_release_defaults_stay_in_sync_with_backend_settings() -> None:
    """RELEASE_DEFAULTS 与 config.py 的字段默认值漂移的话，提示要么误报要么漏报。"""
    from app.core.config import Settings

    fields = Settings.model_fields
    assert release.RELEASE_DEFAULTS["APP_ENV"] == fields["app_env"].default
    assert release.RELEASE_DEFAULTS["APP_WORKERS"] == str(fields["app_workers"].default)
    assert release.RELEASE_DEFAULTS["SCHEDULER_MODE"] == fields["scheduler_mode"].default
    for key, field_name in (
        ("RATE_LIMIT_ENABLED", "rate_limit_enabled"),
        ("AUTO_MIGRATE", "auto_migrate"),
        ("REDIS_ENABLED", "redis_enabled"),
    ):
        expected = str(fields[field_name].default).lower()
        assert release.RELEASE_DEFAULTS[key] == expected, f"{key} 与 Settings.{field_name} 默认值不一致"
