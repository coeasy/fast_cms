from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import metrics as _metrics
from app.core.config import settings
from app.core.errors import get_request_id

logger = logging.getLogger("fastcms.db.sql")

if settings.is_sqlite:
    Path(settings.db_url.split("///")[-1]).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.db_url,
    echo=settings.db_echo,
    future=True,
    pool_pre_ping=not settings.is_sqlite,
    **({} if settings.is_sqlite else {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow}),
)

# ---- 慢 SQL 日志（B-2 / Phase 11-2）----------------------------------------
#
# 「请求慢」最常见的根因是某条 SQL 慢，但请求级慢日志只能给到总耗时。
# 这里在语句级再挂一层：超过 slow_sql_threshold_ms 的语句打 WARNING，
# 带上 request_id，与慢请求日志、审计日志三者按 request_id 串成一条线。
# 钩子挂在 sync_engine 上——async 引擎执行语句时最终也会走这对钩子，
# 且回调在 greenlet 内同步执行，ContextVar（request_id）取得到。
def register_slow_sql_hooks(sync_engine) -> None:
    """给一个引擎挂上慢 SQL 钩子（全局引擎导入时自动调用；测试重建引擎后必须补挂）。"""
    if settings.db_echo:  # echo 模式下每条语句本来就打日志，无需重复
        return

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _slow_sql_before(_conn, _cursor, _statement, _parameters, context, _executemany) -> None:
        context._fastcms_query_started = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _slow_sql_after(_conn, _cursor, statement, _parameters, context, _executemany) -> None:
        started = getattr(context, "_fastcms_query_started", None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms < settings.slow_sql_threshold_ms:
            return
        logger.warning(
            "慢SQL request_id=%s duration_ms=%.1f statement=%s",
            get_request_id(),
            elapsed_ms,
            " ".join(statement.split())[:500],
        )


register_slow_sql_hooks(engine.sync_engine)

# ---- 连接池 gauge（Phase 11-4）---------------------------------------------
#
# 连接池耗尽的表现是「所有请求突然一起变慢」，等看到慢日志已经晚了。
# 把「当前借出数 / 池容量」暴露成 gauge，容量告警可以提前于故障。
def _pool_value(fn_name: str) -> float:
    pool = engine.pool
    fn = getattr(pool, fn_name, None)
    if fn is None:
        return 0.0
    try:
        return float(fn())
    except Exception:
        return 0.0


_metrics.register_gauge(
    "fastcms_db_pool_checked_out",
    "Currently checked-out database connections.",
    lambda: _pool_value("checkedout"),
)
_metrics.register_gauge(
    "fastcms_db_pool_size",
    "Configured base size of the database connection pool.",
    lambda: _pool_value("size"),
)

if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def database_dialect() -> str:
    """返回当前数据库方言名（sqlite / mysql / postgresql …）。

    监控与诊断输出统一走这里，避免各模块用 ``is_sqlite`` 做二值判断把
    第三种方言误标成 mysql。
    """
    return engine.dialect.name


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            # BaseException 而非 Exception：commit() 被取消时抛的是 CancelledError，
            # 不在这里回滚，会话会带着未完成事务被 close，调用方若在同一会话上
            # 继续查询就会读到半提交状态（SQLite 上表现为锁不释放）。
            await session.rollback()
            raise
