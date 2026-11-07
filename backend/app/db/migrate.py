from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.core.boot_lock import BootLock
from app.core.config import BASE_DIR, settings
from app.db.session import engine


def backup_dir() -> Path:
    """迁移备份目录跟随数据根：DATA_DIR 搬迁后备份自动落到新位置。

    用函数而不是模块常量，保证「运行时改 DATA_DIR」也生效，且不被 import 时机固化。
    """
    return settings.data_root / "backups"


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
        backup_dir().glob(f"{stem}-*.db"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[keep:]
    for path in stale:
        path.unlink(missing_ok=True)


def backup_sqlite() -> Path | None:
    path = _sqlite_path()
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None
    backup_dir().mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir() / f"{path.stem}-{stamp}.db"
    counter = 1
    while target.exists():
        target = backup_dir() / f"{path.stem}-{stamp}-{counter}.db"
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


async def check_database() -> tuple[str | None, str]:
    """只读检查迁移状态，返回 (当前版本, 目标版本)；不迁移、不备份、不写库。

    ``fastcms db check`` 用：保证“检查”语义是只读的，避免 dev 环境下
    ``ensure_database()`` 悄悄升级数据库（检查命令不应产生写副作用）。
    """
    tables, current = await _database_state()
    head = head_revision()
    if not tables:
        return None, head
    if "alembic_version" not in tables:
        raise RuntimeError(
            "数据库包含数据表但缺少 Alembic 版本记录。请使用 `python -m app.cli db upgrade` "
            "或重建数据库后执行 `python -m app.cli init`。"
        )
    return current, head


@asynccontextmanager
async def migration_lock() -> AsyncIterator[None]:
    """数据库级迁移锁：多副本并发执行 ``db upgrade`` 时只允许一个真正迁移。

    - MySQL：``GET_LOCK('fastcms:migrate', 300)``，连接级，锁随连接释放；
    - SQLite：跨进程文件锁（``BootLock``），适配共享卷下多容器/多 worker。

    拿不到锁的进程会等待；拿到后发现已到 head 则 Alembic 升级为空操作，
    因此「两个副本同时启动只迁移一次」成立。
    """
    if settings.is_sqlite:
        lock = BootLock(settings.data_root / "migrate.lock")
        if not await asyncio.to_thread(lock.acquire):
            raise RuntimeError("等待迁移锁超时：可能有其他副本正在执行迁移")
        try:
            yield
        finally:
            lock.release()
        return
    async with engine.connect() as connection:
        got = (await connection.execute(text("SELECT GET_LOCK('fastcms:migrate', 300)"))).scalar()
        if got != 1:
            raise RuntimeError("获取 MySQL 迁移锁失败（GET_LOCK 超时或不可用）")
        try:
            yield
        finally:
            await connection.execute(text("SELECT RELEASE_LOCK('fastcms:migrate')"))
