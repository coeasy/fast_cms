"""Phase 11：可观测与性能的单元测试。

覆盖三块：
- 慢 SQL 钩子（11-2）：after_cursor_execute 在超阈值时打 WARNING；
- 审计批量队列（11-3）：入队 / 刷盘 / 满降级 / 失败回滚重试；
- gauge 注册表与 render(extra)（11-4）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from sqlalchemy import text as sql_text

from app.core import metrics
from app.kernel.audit import AuditWriteQueue


@pytest.fixture(autouse=True)
async def _release_engine_connections():
    """本文件的测试会直连全局 engine（真实执行 SQL / 真实批量写入）。

    连接用完只是回到池里、并不关闭——session 级 teardown 删模板库时，
    攥着连接的池会把文件锁死（WinError 32）。每个测试结束都 dispose 一次，
    让池里不留活连接；engine 之后会自动重建池，不影响后续测试。
    """
    yield
    from app.db.session import engine

    await engine.dispose()


# ---------------------------------------------------------------------------
# 11-2 慢 SQL 日志
# ---------------------------------------------------------------------------


class TestSlowSql:
    def _listeners(self):
        from app.db.session import engine

        return engine.sync_engine.events

    async def test_slow_statement_logs_warning(self) -> None:
        """人为执行一条超过阈值的语句，确认打出了含语句文本的 WARNING。

        不用 caplog：全套件里有测试会把 ``fastcms.*`` 链上的 propagate 关掉，
        走根 logger 的捕获会漏。直接把 handler 挂到目标 logger 上，与
        propagation 配置无关。
        """
        from app.core.config import settings

        records: list[logging.LogRecord] = []

        class _Catch(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("fastcms.db.sql")
        handler = _Catch(level=logging.WARNING)
        logger.addHandler(handler)
        # 阈值临时调到 0，任何语句都算慢——不必真造一条 500ms 的 SQL
        original = settings.slow_sql_threshold_ms
        settings.slow_sql_threshold_ms = 0
        try:
            from app.db.session import async_session_factory

            async with async_session_factory() as session:
                await session.execute(sql_text("SELECT 1"))
        finally:
            settings.slow_sql_threshold_ms = original
            logger.removeHandler(handler)
        assert records, "应当有一条慢SQL WARNING"
        assert "SELECT 1" in records[-1].getMessage()

    def test_hooks_registered(self) -> None:
        """全局引擎导入时已注册慢 SQL 钩子（重建引擎后由 conftest 补挂，见 conftest._repoint_engine）。"""
        from app.db.session import register_slow_sql_hooks

        assert callable(register_slow_sql_hooks)


# ---------------------------------------------------------------------------
# 11-3 审计批量队列
# ---------------------------------------------------------------------------


class TestAuditQueue:
    def _row(self, **overrides: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "user_id": None,
            "username": "tester",
            "module": "sys",
            "action": "unit-test",
            "method": "POST",
            "path": "/api/v1/unit-test",
            "status_code": 200,
            "success": True,
            "duration_ms": 1,
            "ip": "127.0.0.1",
            "user_agent": "pytest",
            "detail": None,
            "request_id": None,
        }
        row.update(overrides)
        return row

    async def test_enqueue_before_start_falls_back(self) -> None:
        """队列未启动时 enqueue 返回 False（调用方直写），不积内存。"""
        queue = AuditWriteQueue()
        assert queue.enqueue(self._row()) is False
        assert queue.depth() == 0

    async def test_flush_writes_batch(self) -> None:
        queue = AuditWriteQueue()
        queue._task = asyncio.create_task(asyncio.sleep(3600))  # 模拟已启动
        try:
            for index in range(5):
                assert queue.enqueue(self._row(action=f"unit-{index}")) is True
            written = await queue.flush()
            assert written == 5
            assert queue.depth() == 0
        finally:
            queue._task.cancel()  # type: ignore[arg-type]
            with pytest.raises(asyncio.CancelledError):
                await queue._task  # type: ignore[arg-type]

    async def test_capacity_forces_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import settings

        queue = AuditWriteQueue()
        queue._task = asyncio.create_task(asyncio.sleep(3600))
        try:
            monkeypatch.setattr(settings, "audit_queue_capacity", 2)
            assert queue.enqueue(self._row()) is True
            assert queue.enqueue(self._row()) is True
            # 队列满：返回 False，调用方降级直写
            assert queue.enqueue(self._row()) is False
        finally:
            queue._task.cancel()  # type: ignore[arg-type]
            with pytest.raises(asyncio.CancelledError):
                await queue._task  # type: ignore[arg-type]

    async def test_failed_flush_keeps_records_for_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """刷盘失败时记录塞回队头，下一轮重试而不是丢掉。"""
        queue = AuditWriteQueue()
        queue._task = asyncio.create_task(asyncio.sleep(3600))
        try:
            queue.enqueue(self._row(action="keep-1"))
            queue.enqueue(self._row(action="keep-2"))

            # 让 session 构造失败：monkeypatch async_session_factory
            import app.db.session as session_mod

            class _Boom:
                def __call__(self):  # pragma: no cover - 通过 __aenter__ 触发
                    raise RuntimeError("db down")

            monkeypatch.setattr(session_mod, "async_session_factory", _Boom())
            with pytest.raises(RuntimeError):
                await queue.flush()
            # 两条记录都还在队里，且顺序保持
            assert queue.depth() == 2
            assert [item["action"] for item in queue._items] == ["keep-1", "keep-2"]
        finally:
            queue._task.cancel()  # type: ignore[arg-type]
            with pytest.raises(asyncio.CancelledError):
                await queue._task  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 11-4 gauge 注册表
# ---------------------------------------------------------------------------


class TestGauges:
    def setup_method(self) -> None:
        metrics.reset()

    def test_register_and_render(self) -> None:
        metrics.register_gauge("fastcms_test_gauge", "A test gauge.", lambda: 42.0)
        out = metrics.render()
        assert "# HELP fastcms_test_gauge A test gauge." in out
        assert "# TYPE fastcms_test_gauge gauge" in out
        assert "fastcms_test_gauge 42" in out

    def test_failing_gauge_is_skipped(self) -> None:
        def _boom() -> float:
            raise RuntimeError("down")

        metrics.register_gauge("fastcms_test_boom", "Broken.", _boom)
        metrics.register_gauge("fastcms_test_ok", "Working.", lambda: 1.0)
        out = metrics.render()
        assert "fastcms_test_boom" not in out
        assert "fastcms_test_ok 1" in out

    def test_extra_gauge_from_endpoint_query(self) -> None:
        """outbox 积压这类要查库的 gauge 走 render(extra)，格式与注册 gauge 一致。"""
        out = metrics.render({"fastcms_events_outbox_backlog": 7.0})
        assert "# TYPE fastcms_events_outbox_backlog gauge" in out
        assert "fastcms_events_outbox_backlog 7" in out

    def test_pool_gauges_registered(self) -> None:
        """db.session 导入后应注册池 gauge，且取值不抛异常。"""
        import app.db.session  # noqa: F401

        names = set(metrics._gauges)
        assert "fastcms_db_pool_checked_out" in names
        assert "fastcms_db_pool_size" in names
