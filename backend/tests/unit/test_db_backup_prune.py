from __future__ import annotations

import os
import time

from app.db import migrate


def _make_backup(directory, name: str, age_seconds: float) -> None:
    path = directory / name
    path.write_bytes(b"sqlite")
    past = time.time() - age_seconds
    os.utime(path, (past, past))


def test_prune_backups_keeps_newest_files(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "BACKUP_DIR", tmp_path)
    _make_backup(tmp_path, "fastcms-20260101-000000.db", 400)
    _make_backup(tmp_path, "fastcms-20260101-000100.db", 300)
    _make_backup(tmp_path, "fastcms-20260101-000200.db", 200)
    _make_backup(tmp_path, "fastcms-20260101-000300.db", 100)

    migrate._prune_backups("fastcms", 2)

    remaining = sorted(path.name for path in tmp_path.glob("*.db"))
    assert remaining == ["fastcms-20260101-000200.db", "fastcms-20260101-000300.db"]


def test_prune_backups_zero_keeps_all_and_ignores_other_stems(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "BACKUP_DIR", tmp_path)
    _make_backup(tmp_path, "fastcms-20260101-000000.db", 200)
    _make_backup(tmp_path, "fastcms-20260101-000100.db", 100)
    _make_backup(tmp_path, "test_fastcms-20260101-000000.db", 100)

    # 0 = 不清理；且只按同名数据库前缀清理，测试库备份不受开发库影响
    migrate._prune_backups("fastcms", 0)
    assert len(list(tmp_path.glob("*.db"))) == 3

    migrate._prune_backups("fastcms", 1)
    assert [path.name for path in tmp_path.glob("*.db")] == [
        "fastcms-20260101-000100.db",
        "test_fastcms-20260101-000000.db",
    ]
