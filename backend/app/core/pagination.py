"""分页组件。

P1-5：每个列表都执行全量 ``count(*)`` 子查询，大表（日志/审计/通知）深分页退化。
保留 offset 兼容模式，同时提供：

- ``total=false``：跳过 count，响应不携带 ``total`` 字段（契约声明 total 可选）；
- :func:`keyset_paginate`：游标分页，深分页不再做深偏移扫描。

导出与列表分离：导出走 :mod:`app.core.export` 的批量语句，不复用列表 count。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Query
from sqlalchemy import ColumnElement, Select, UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnClause


@dataclass
class PageParams:
    page: int = Query(default=1, ge=1)
    page_size: int = Query(default=20, ge=1, le=100)
    # total=false 时跳过 count 子查询，响应不返回 total 字段（大表列表降本）。
    total: bool = Query(default=True)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_data(items: Sequence[Any], total: int | None, params: PageParams) -> dict[str, Any]:
    """构造列表响应。

    ``total`` 为 ``None``（调用方用 ``total=false`` 跳过 count）时不输出该字段，
    让契约明确「total 可选」；否则始终输出数字。
    """
    data: dict[str, Any] = {
        "items": list(items),
        "page": params.page,
        "page_size": params.page_size,
    }
    if total is not None:
        data["total"] = total
    return data


async def count_rows(db: AsyncSession, statement: Select) -> int:
    """统计语句的总行数。

    ``paginate`` 与「需要额外列、不能用 scalars 解包」的列表（如文件列表要带
    上传者用户名）共用这一个入口，避免各处手写 count 子查询导致口径漂移
    （例如某处漏掉 ``order_by(None)``，深分页时子查询白排一次序）。
    """
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    return int((await db.execute(count_statement)).scalar() or 0)


async def paginate(
    db: AsyncSession,
    statement: Select,
    params: PageParams,
) -> tuple[list[Any], int | None]:
    """offset 分页；``params.total=False`` 时返回 ``(rows, None)`` 并跳过 count。

    返回的 ``total`` 为 ``None`` 表示调用方不应输出 ``total`` 字段。
    """
    total: int | None = None
    if params.total:
        total = await count_rows(db, statement)
    rows: Sequence[Any] = (
        (await db.execute(statement.offset(params.offset).limit(params.page_size)))
        .scalars()
        .unique()
        .all()
    )
    return list(rows), total


def _mapped_column(key_col: Any) -> Any | None:
    """把 keyset 键解析成它背后的 ``Column``；无法解析（纯表达式）返回 ``None``。"""
    if isinstance(key_col, InstrumentedAttribute):
        return key_col.property.columns[0]
    if isinstance(key_col, ColumnClause):
        return key_col
    return None


def _assert_unique_key(key_col: Any) -> None:
    """keyset 分页的键必须是唯一键（B-9 / 3-4）。

    游标分页用 ``(key_col > cursor)`` 做边界，如果键不唯一，同值的多行会被
    ``LIMIT`` 从页间切开或漏掉，产生不稳定分页。因此：

    - 主键、带 UNIQUE 约束、带唯一索引的列都合法；
    - 普通索引列（如 ``created_at`` 只建了普通索引）立即报错，杜绝「复用了
      非唯一键但没发现」的静默退化；
    - 无法解析成具体列的表达式（如 ``func.lower(col)``）同样拒绝：无法证明唯一。
    """
    column = _mapped_column(key_col)
    if column is None:
        raise ValueError(f"keyset 分页键必须是映射列，收到：{key_col!r}")
    if getattr(column, "primary_key", False):
        return
    table = getattr(column, "table", None)
    if table is not None:
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and any(
                column is col for col in constraint.columns
            ):
                return
        for index in table.indexes:
            if getattr(index, "unique", False) and any(column is col for col in index.columns):
                return
    raise ValueError(
        f"keyset 分页键必须唯一（主键/唯一约束/唯一索引），收到非唯一列：{column.key}"
    )


async def keyset_paginate(
    db: AsyncSession,
    statement: Select,
    *,
    key_col: ColumnElement[Any] | InstrumentedAttribute[Any],
    cursor: Any | None = None,
    limit: int = 20,
    ascending: bool = True,
) -> tuple[list[Any], Any | None]:
    """Keyset（游标）分页：``WHERE (key_col > cursor) ORDER BY key_col LIMIT limit``。

    - ``cursor`` 为上一页返回的 ``next_cursor``，首页传 ``None``；
    - 返回 ``(rows, next_cursor)``，``next_cursor`` 为 ``None`` 表示没有更多数据；
    - 适合大表深分页：不执行全量 count，也不做深偏移扫描。

    要求 ``statement`` 选择单一实体（可用 ``.scalars()`` 解包），且 ``key_col``
    必须是唯一键（主键/唯一约束/唯一索引）——否则抛 ``ValueError``（B-9）。
    """
    _assert_unique_key(key_col)
    stmt = statement
    if cursor is not None:
        op = key_col > cursor if ascending else key_col < cursor
        stmt = stmt.where(op)
    stmt = stmt.order_by(key_col.asc() if ascending else key_col.desc()).limit(limit)
    rows: Sequence[Any] = (await db.execute(stmt)).scalars().unique().all()
    key_attr: str = key_col.key or ""
    next_cursor = getattr(rows[-1], key_attr, None) if rows else None
    return list(rows), next_cursor
