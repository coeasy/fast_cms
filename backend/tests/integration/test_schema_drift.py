from __future__ import annotations

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.db.base import Base
from tests.conftest import TEST_DB


def _table_name(directive: tuple[Any, ...]) -> str | None:
    for element in directive:
        name = getattr(element, "name", None)
        if isinstance(name, str) and hasattr(element, "columns"):
            return name
    return None


def test_migrations_cover_every_model_column(client: TestClient) -> None:
    """模型加了字段却忘了写迁移时接口不会报错，线上建表才缺列，所以直接比对已迁移库与元数据。"""
    engine = create_engine(f"sqlite:///{TEST_DB.as_posix()}")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()

    # 其他用例会在这同一个测试库里建 demo_ 业务表供代码生成使用，那些表本来就不该有迁移
    managed = set(Base.metadata.tables)
    relevant = [
        item
        for item in diff
        if (_table_name(item) is None or _table_name(item) in managed)
    ]
    assert relevant == [], f"模型与迁移不一致：{relevant}"
