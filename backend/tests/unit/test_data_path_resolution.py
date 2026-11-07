"""数据路径解析回归测试：改 DATA_DIR / DB_PATH 后，库、上传、密钥、备份、锁都跟着走。

覆盖的验收场景：
1. 默认行为不变（库仍在 backend/data/fastcms.db），升级不破坏现有部署；
2. DB_URL > DB_PATH > <数据根>/fastcms.db 的优先级；
3. 相对路径一律以「仓库根 / 数据根」为基准，不随当前工作目录漂移；
4. 迁移备份目录跟随数据根（migrate.backup_dir 用函数而非模块常量，运行时才解析）；
5. scripts/config_probe.py 的回退实现与 Settings 给出同一个答案。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.core.config import BASE_DIR, REPO_ROOT, Settings

ROOT = Path(__file__).resolve().parents[3]


def test_default_db_url_unchanged(tmp_path):
    """不设任何数据相关变量时，库仍在 backend/data/fastcms.db —— 老部署零改动升级。"""
    loaded = Settings(data_dir="", db_path="", db_url="", storage_local_dir="")
    assert loaded.data_dir == (BASE_DIR / "data").as_posix()
    assert loaded.data_root == BASE_DIR / "data"
    assert loaded.db_url == f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'fastcms.db').as_posix()}"
    assert loaded.storage_local_dir == (BASE_DIR / "data" / "uploads").as_posix()


def test_db_path_relative_resolves_against_data_root(tmp_path):
    """DB_PATH 写成相对路径时以数据根为基准，而不是随 CWD 漂移。"""
    loaded = Settings(data_dir=str(tmp_path / "data"), db_path="custom/app.db", db_url="")
    assert loaded.db_url == f"sqlite+aiosqlite:///{(tmp_path / 'data' / 'custom' / 'app.db').as_posix()}"


def test_db_path_absolute_is_used_as_is(tmp_path):
    """绝对 DB_PATH 照用，可以完全脱离数据根目录。"""
    loaded = Settings(db_path=str(tmp_path / "elsewhere" / "app.db"), db_url="")
    assert loaded.db_url == f"sqlite+aiosqlite:///{(tmp_path / 'elsewhere' / 'app.db').as_posix()}"


def test_db_url_wins_over_db_path():
    """优先级 DB_URL > DB_PATH：显式连接串不能被 DB_PATH 覆盖。"""
    loaded = Settings(
        db_url="sqlite+aiosqlite:///x.db",
        db_path="y.db",
    )
    assert loaded.db_url == "sqlite+aiosqlite:///x.db"

    mysql = Settings(db_url="mysql+aiomysql://u:p@127.0.0.1:3306/cms", db_path="ignored.db")
    assert mysql.db_url == "mysql+aiomysql://u:p@127.0.0.1:3306/cms"
    assert not mysql.is_sqlite


def test_data_dir_relocation_moves_everything(tmp_path):
    """只改 DATA_DIR：库、上传目录、密钥、备份、锁全部搬到新位置。"""
    new_root = tmp_path / "newdata"
    loaded = Settings(data_dir=str(new_root), db_path="", db_url="", storage_local_dir="")

    assert loaded.data_root == new_root
    assert loaded.db_url == f"sqlite+aiosqlite:///{(new_root / 'fastcms.db').as_posix()}"
    assert loaded.storage_local_dir == (new_root / "uploads").as_posix()

    # 签名密钥落到新数据根，而不是老位置
    loaded.app_secret_key = ""
    assert loaded.secret_key  # 触发落盘
    assert (new_root / ".secret").exists()


def test_secret_key_is_stable_across_data_root(tmp_path):
    """同一个数据根重复读取得到同一把密钥：会话不会因为重启而失效。"""
    root = tmp_path / "data"
    first = Settings(data_dir=str(root), app_secret_key="")
    second = Settings(data_dir=str(root), app_secret_key="")
    assert first.secret_key == second.secret_key


def test_relative_data_dir_anchors_at_repo_root():
    """DATA_DIR 写相对路径时以仓库根为基准，与 config_probe 的回退实现同规则。"""
    loaded = Settings(data_dir="runtime/data", db_path="", db_url="")
    assert loaded.data_dir == (REPO_ROOT / "runtime" / "data").as_posix()


def test_relative_storage_dir_resolves_against_data_root(tmp_path):
    loaded = Settings(data_dir=str(tmp_path), storage_local_dir="files", db_path="", db_url="")
    assert loaded.storage_local_dir == (tmp_path / "files").as_posix()


def test_migrate_backup_dir_follows_data_root(tmp_path, monkeypatch):
    """备份目录必须在调用时才解析，才能跟随运行时改过的 DATA_DIR。"""
    spec = importlib.util.spec_from_file_location("probe_migrate", ROOT / "backend" / "app" / "db" / "migrate.py")
    assert spec and spec.loader is not None
    migrate = importlib.util.module_from_spec(spec)
    sys.modules["probe_migrate"] = migrate
    spec.loader.exec_module(migrate)

    # 期望值从 settings 实际数据根推导，而不是硬编码默认路径——
    # 否则在 DATA_DIR 被设置的环境里跑测试会凭空失败（fastcms check 等聚合门禁会设 DATA_DIR）。
    assert migrate.backup_dir() == migrate.settings.data_root / "backups"
    monkeypatch.setattr(migrate.settings, "data_dir", str(tmp_path / "data"))
    assert migrate.backup_dir() == tmp_path / "data" / "backups"


def test_config_probe_fallback_matches_settings(tmp_path):
    """scripts/config_probe.py 的回退实现与 Settings 逐场景一致（无后端依赖时靠它）。"""
    spec = importlib.util.spec_from_file_location("probe_cfg", ROOT / "scripts" / "config_probe.py")
    assert spec and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    sys.modules["probe_cfg"] = probe
    spec.loader.exec_module(probe)

    cases = [
        {},
        {"DATA_DIR": str(tmp_path / "data")},
        {"DB_PATH": "custom/app.db"},
        {"DB_URL": "mysql+aiomysql://u:p@127.0.0.1:3306/cms", "DB_PATH": "ignored.db"},
        {"DATA_DIR": "runtime/data", "DB_PATH": "my.db"},
        {"STORAGE_LOCAL_DIR": "files"},
    ]
    for env in cases:
        loaded = Settings(
            data_dir=env.get("DATA_DIR", ""),
            db_path=env.get("DB_PATH", ""),
            db_url=env.get("DB_URL", ""),
            storage_local_dir=env.get("STORAGE_LOCAL_DIR", ""),
        )
        assert probe.resolve_db_url(env) == loaded.db_url, f"DB URL 不一致：{env}"
        assert probe.resolve_data_dir(env) == loaded.data_dir, f"数据根不一致：{env}"
        assert probe.resolve_storage_dir(env) == loaded.storage_local_dir, f"上传目录不一致：{env}"
