"""`app.cli backup` 的归档回归测试。

审计文档 §5 指出备份只覆盖 SQLite 库、不含 uploads，恢复时会出现
「库回来了、附件全丢」的半套备份。`backup --with-uploads` 把库快照与
上传目录打成一个 zip，本测试锁定：

1. zip 内同时含 ``db/<快照>.db`` 与 ``uploads/**``；
2. 库条目非空——早期实现会把 zip 自己当快照打进去（自引用、0 字节）；
3. ``--output`` 指向 .zip 时，快照落在同目录的 .db，不污染归档内容。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app import cli
from app.core.config import settings


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "app.db"
    db_path.write_bytes(b"SQLite format 3\x00" + b"x" * 2048)
    uploads = tmp_path / "uploads" / "2026" / "10"
    uploads.mkdir(parents=True)
    (uploads / "a.png").write_bytes(b"fake-image-bytes")
    return db_path, tmp_path / "uploads"


def test_backup_with_uploads_archives_db_and_files(tmp_path: Path, monkeypatch) -> None:
    db_path, uploads = _prepare(tmp_path)
    monkeypatch.setattr(settings, "db_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(settings, "storage_local_dir", str(uploads))

    target = tmp_path / "bundle.zip"
    cli._backup(str(target), with_uploads=True)

    assert target.exists()
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert "db/bundle.db" in names
        assert "uploads/2026/10/a.png" in names
        # 自引用回归：库条目必须真的带数据
        assert archive.getinfo("db/bundle.db").file_size > 0
        assert archive.read("uploads/2026/10/a.png") == b"fake-image-bytes"


def test_backup_without_uploads_stays_plain_copy(tmp_path: Path, monkeypatch) -> None:
    db_path, uploads = _prepare(tmp_path)
    monkeypatch.setattr(settings, "db_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(settings, "storage_local_dir", str(uploads))

    target = tmp_path / "plain.db"
    cli._backup(str(target), with_uploads=False)

    assert target.read_bytes() == db_path.read_bytes()
    assert not (tmp_path / "plain.zip").exists()


def test_backup_with_uploads_tolerates_missing_upload_dir(tmp_path: Path, monkeypatch) -> None:
    db_path, _ = _prepare(tmp_path)
    monkeypatch.setattr(settings, "db_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path / "nope"))

    target = tmp_path / "bundle.zip"
    cli._backup(str(target), with_uploads=True)

    with zipfile.ZipFile(target) as archive:
        assert [n for n in archive.namelist() if n.startswith("uploads/")] == []
        assert archive.getinfo("db/bundle.db").file_size > 0
