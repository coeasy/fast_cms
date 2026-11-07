"""P1-3：迁移 up/down/up 演练——回滚可靠性 + 重建后 schema 无漂移。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect

from app.core.config import settings
from app.db.migrate import alembic_config


def _sync_url(db_path: Path) -> str:
    return f"sqlite:///{db_path.as_posix()}"


def _schema(db_path: Path) -> dict[str, list]:
    engine = create_engine(_sync_url(db_path))
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    schema: dict[str, list] = {}
    for table in tables:
        if table == "alembic_version":
            continue
        columns = sorted(
            (col["name"], str(col["type"]), bool(col.get("nullable")))
            for col in inspector.get_columns(table)
        )
        schema[table] = columns
    engine.dispose()
    return schema


def _current_revision(db_path: Path) -> str | None:
    engine = create_engine(_sync_url(db_path))
    with engine.connect() as connection:
        result = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    engine.dispose()
    return result


def test_full_round_trip_reproduces_schema(tmp_path: Path, monkeypatch) -> None:
    """upgrade head → downgrade base → upgrade head：schema 完全一致且版本回到 head。

    P1-3 验收：回滚可靠，用完 server_default 后能干净重建，不残留默认值语义。
    """
    db_path = tmp_path / "drill.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setattr(settings, "db_url", url)
    cfg = alembic_config()

    command.upgrade(cfg, "head")
    head = _current_revision(db_path)
    assert head is not None, "首次 upgrade 后应存在 alembic_version"
    first_schema = _schema(db_path)
    assert first_schema, "首次 upgrade 后不应为空"

    # 全量回滚到 base（执行所有 downgrade()）
    command.downgrade(cfg, "base")
    assert _schema(db_path) == {}, "downgrade base 后应清空业务表（仅剩 alembic_version）"

    # 再次升级到 head
    command.upgrade(cfg, "head")
    assert _current_revision(db_path) == head, "二次 upgrade 后版本应回到 head"
    assert _schema(db_path) == first_schema, "up/down/up 后 schema 应与首次一致（无漂移）"


def test_single_step_down_up_is_stable(tmp_path: Path, monkeypatch) -> None:
    """降一个版本再升回来，schema 不变（验证最近一次迁移的 down/up 对称性）。"""
    db_path = tmp_path / "step.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setattr(settings, "db_url", url)
    cfg = alembic_config()

    command.upgrade(cfg, "head")
    first_schema = _schema(db_path)

    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    assert _schema(db_path) == first_schema, "单步 down/up 后 schema 应一致"
