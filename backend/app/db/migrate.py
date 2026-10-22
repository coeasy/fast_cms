from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.core.config import BASE_DIR, DATA_DIR, settings
from app.db.session import engine

BACKUP_DIR = DATA_DIR / "backups"


def alembic_config() -> Config:
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.db_url)
    return config


def head_revision() -> str:
    script = ScriptDirectory.from_config(alembic_config())
    revision = script.get_current_head()
    if revision is None:
        raise RuntimeError("未找到 Alembic 迁移脚本")
    return revision


def _sqlite_path() -> Path | None:
    if not settings.is_sqlite:
        return None
    raw = settings.db_url.split("///", 1)[-1]
    return Path(raw)


def _prune_backups(stem: str, keep: int) -> None:
    """dev 环境每次改表都会备份，不设上限的话 backups/ 会一直膨胀；keep<=0 表示不清理。"""
    if keep <= 0:
        return
    stale = sorted(
        BACKUP_DIR.glob(f"{stem}-*.db"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[keep:]
    for path in stale:
        path.unlink(missing_ok=True)


def backup_sqlite() -> Path | None:
    path = _sqlite_path()
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"{path.stem}-{stamp}.db"
    counter = 1
    while target.exists():
        target = BACKUP_DIR / f"{path.stem}-{stamp}-{counter}.db"
        counter += 1
    source_connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        with sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)
    finally:
        source_connection.close()
    _prune_backups(path.stem, settings.db_backup_keep)
    return target


async def _database_state() -> tuple[set[str], str | None]:
    async with engine.connect() as connection:
        tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        revision: str | None = None
        if "alembic_version" in tables:
            revision = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    return tables, revision


async def _upgrade(reason: str) -> None:
    if settings.is_sqlite:
        backup = backup_sqlite()
        if backup is not None:
            print(f"[db] SQLite 备份已创建：{backup}")
    print(f"[db] 执行数据库迁移（{reason}）...")
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    print("[db] 数据库迁移完成")


async def ensure_database() -> None:
    tables, current = await _database_state()
    head = head_revision()
    if not tables:
        await _upgrade("初始化空数据库")
        return
    if "alembic_version" not in tables:
        raise RuntimeError(
            "数据库包含数据表但缺少 Alembic 版本记录。请使用 `python -m app.cli db upgrade` "
            "或重建数据库后执行 `python -m app.cli init`。"
        )
    if current == head:
        return
    if settings.app_env == "dev" or settings.auto_migrate:
        await _upgrade(f"{current or 'empty'} -> {head}")
        return
    raise RuntimeError(
        f"数据库迁移版本落后（当前 {current or '空'}，目标 {head}）。"
        "生产环境请先执行 `python -m app.cli db upgrade`。"
    )
