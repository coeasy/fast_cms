"""`python scripts/start.py server` 的一键部署回归测试。

关键契约：clone 后没有 .env 也能一条命令对外提供服务 —— 首次运行自动生成
production + 0.0.0.0 + 随机强口令 + 关闭接口文档的 .env；已存在的 .env 一律
尊重、只做体检提醒，绝不覆盖用户已有的任何一行。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import env_check
import install
import start

EXAMPLE_BODY = (
    "APP_ENV=dev\n"
    "APP_HOST=127.0.0.1\n"
    "APP_PORT=8000\n"
    "APP_SECRET_KEY=\n"
    "INITIAL_ADMIN_PASSWORD=\n"
    "APP_TIMEZONE=Asia/Shanghai\n"
    "AUTH_COOKIE_SECURE=\n"
    "# RETENTION_DAYS=30\n"
)


def _patch_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(start, "ROOT", root)
    monkeypatch.setattr(install, "ROOT", root)
    monkeypatch.setattr(env_check, "ROOT", root)


def _read_env(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (root / ".env").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            out[key.strip()] = value.strip()
    return out


def test_fresh_clone_generates_hardened_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """全新目录（无 .env）：server 自动生成可对外服务的生产配置。"""
    (tmp_path / ".env.example").write_text(EXAMPLE_BODY, encoding="utf-8")
    _patch_root(monkeypatch, tmp_path)

    password = start.ensure_server_env(allow_http=False)
    env = _read_env(tmp_path)

    assert env.get("APP_ENV") == "production"
    assert env.get("APP_HOST") == "0.0.0.0"
    assert env.get("APP_DOCS_ENABLED") == "false"
    assert env.get("APP_SECRET_KEY"), "必须生成签名密钥"
    assert password and len(password) >= 12, "必须生成强口令"
    assert env.get("INITIAL_ADMIN_PASSWORD") == password, "生成的配置里口令与提示一致"


def test_server_env_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """重复调用不得改写已有配置（尤其不得更换口令）。"""
    (tmp_path / ".env.example").write_text(EXAMPLE_BODY, encoding="utf-8")
    _patch_root(monkeypatch, tmp_path)
    start.ensure_server_env(allow_http=False)
    before = _read_env(tmp_path)

    start.ensure_server_env(allow_http=False)
    assert _read_env(tmp_path) == before, "第二次运行不应改动任何一行"


def test_server_env_http_disables_secure_cookie(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--http 场景：明文 HTTP 下必须显式关闭 Secure Cookie，否则登录后 15 分钟掉线。"""
    (tmp_path / ".env.example").write_text(EXAMPLE_BODY, encoding="utf-8")
    _patch_root(monkeypatch, tmp_path)

    start.ensure_server_env(allow_http=True)
    assert _read_env(tmp_path).get("AUTH_COOKIE_SECURE") == "false"


def test_existing_dev_env_is_not_overwritten(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """用户已有的 .env 是权威配置：server 只提醒不覆盖。"""
    (tmp_path / ".env.example").write_text(EXAMPLE_BODY, encoding="utf-8")
    existing = "APP_ENV=dev\nAPP_HOST=127.0.0.1\nAPP_PORT=8123\nMY_CUSTOM=keep-me\n"
    (tmp_path / ".env").write_text(existing, encoding="utf-8")
    _patch_root(monkeypatch, tmp_path)

    assert start.ensure_server_env(allow_http=False) is None
    assert (tmp_path / ".env").read_text(encoding="utf-8") == existing, "已有 .env 不得被改写"


def test_http_flag_requires_server_mode() -> None:
    """--http 只对 server 有效，防止用户在其他模式下误以为 Cookie 被改了。"""
    with pytest.raises(SystemExit) as excinfo:
        start.parse_args(["--http"])
    assert excinfo.value.code == 2  # argparse 用法错误
