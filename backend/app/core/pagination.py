from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class PageParams:
    page: int = Query(default=1, ge=1)
    page_size: int = Query(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_data(items: Sequence[Any], total: int, params: PageParams) -> dict[str, Any]:
    return {
        "items": list(items),
        "page": params.page,
        "page_size": params.page_size,
        "total": total,
    }


async def paginate(db: AsyncSession, statement: Select, params: PageParams) -> tuple[list[Any], int]:
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int((await db.execute(count_statement)).scalar() or 0)
    rows: Sequence[Any] = (
        (await db.execute(statement.offset(params.offset).limit(params.page_size)))
        .scalars()
        .unique()
        .all()
    )
    return list(rows), total
