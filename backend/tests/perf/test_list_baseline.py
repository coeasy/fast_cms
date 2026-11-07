"""B-11 / 4-3：核心列表接口性能基线（默认跳过，``--run-perf`` 时跑）。

基线覆盖 5 个核心列表接口，并用确定性断言回归两处降本收益：

- ``total=false``：少发一次全量 ``COUNT(*)``（查询数减一，非计时断言，杜绝 CI 抖动）；
- keyset 深分页：按 ``id`` 游标翻页不做深偏移扫描（对比偏移分页的 SQL 结构，
  同样是非计时断言）。

SQLite 方言会把 ``limit()`` 编译成 ``LIMIT ? OFFSET ?``，即使没有调用
``offset()`` 也绑定 ``OFFSET 0``——因此「是否生成 OFFSET」不能用 SQL 文本判断，
而必须看**绑定值**：keyset 的 OFFSET 恒为 0，偏移分页第 N 页的 OFFSET 是
``(page-1)*page_size``。断言以绑定值为准，才是可回归的基线。

数据量由 ``PERF_ROWS`` 环境变量控制（默认 3000），只求「足够大到深分页有意义」，
避免把基线测试本身拖慢。计时类断言刻意不用：绝对耗时在 CI 上不可靠，收益以
「查询结构 / 查询数」这类不变量表达。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import app.db.session as session_mod
from app.db.session import session_scope
from app.kernel.models import LoginLog
from tests.conftest import auth

PERF_ROWS = int(os.environ.get("PERF_ROWS", "3000"))
# 五个核心分页列表接口：审计日志 ×2、系统用户、调度任务、调度执行日志。
# 注意：必须是走 page_data 的分页接口（返回 {items,total}）；全量列表接口
# （如 /system/roles 直接返回数组）不属于分页基线，不应纳入。
CORE_LIST_ENDPOINTS = (
    ("登录日志", "/api/v1/monitor/login-logs"),
    ("操作日志", "/api/v1/monitor/operation-logs"),
    ("用户列表", "/api/v1/system/users"),
    ("调度任务", "/api/v1/schedule/jobs"),
    ("调度日志", "/api/v1/schedule/job-logs"),
)


@contextmanager
def _capture_statements() -> Iterator[list[tuple[str, Any]]]:
    """记录引擎上所有 SELECT 的 ``(SQL, 绑定参数)``（挂在当前测试副本引擎上）。

    记录绑定参数是因为 SQLite 方言对 ``limit()`` 也会输出 ``LIMIT ? OFFSET ?``
    （OFFSET 绑定 0），只看 SQL 文本会把 keyset 误判成「带 OFFSET」。
    """
    statements: list[tuple[str, Any]] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    engine = session_mod.engine.sync_engine
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def _bound_limit_offset(sql: str, params: Any) -> tuple[int, int] | None:
    """从 ``LIMIT ? OFFSET ?`` 形式的 SELECT 中取出绑定的 ``(limit, offset)``。

    只匹配「最后两个绑定是 limit/offset」的语句（SQLite 对 limit/offset 用
    位置参数，且总是按 limit 在前、offset 在后的顺序绑定）；非分页 SELECT
    返回 ``None``。
    """
    if not isinstance(params, tuple) or len(params) < 2:
        return None
    tail = sql.upper().rstrip()
    if "LIMIT ?" not in tail or "OFFSET ?" not in tail:
        return None
    return int(params[-2]), int(params[-1])


def _last_login_log_statement(statements: list[tuple[str, Any]]) -> tuple[str, Any] | None:
    """取块内最后一条 sys_login_log 的 SELECT（登录日志页查询总是最后执行）。"""
    for sql, params in reversed(statements):
        if "sys_login_log" in sql.lower():
            return sql, params
    return None


@pytest.fixture
def seeded_logs(client: TestClient) -> int:
    """往当前测试副本直接灌 PERF_ROWS 条登录日志（绕开 API，保证种子确定）。"""
    async def _seed() -> None:
        async with session_scope() as db:
            db.add_all(
                LoginLog(
                    username=f"perf_user_{i % 50}",
                    user_id=None,
                    status="success" if i % 3 else "failure",
                    ip="203.0.113.7",
                )
                for i in range(PERF_ROWS)
            )
            await db.commit()

    import asyncio

    asyncio.run(_seed())
    return PERF_ROWS


@pytest.mark.perf
class TestCoreListBaseline:
    """五个核心列表接口在灌入数据后仍能正常分页（正确性基线）。"""

    @pytest.mark.parametrize("label,path", CORE_LIST_ENDPOINTS)
    def test_list_paginates(self, client: TestClient, admin_token: str, seeded_logs: int, label: str, path: str) -> None:
        headers = auth(admin_token)
        resp = client.get(path, params={"page": 1, "page_size": 10}, headers=headers)
        assert resp.status_code == 200, f"{label}: {resp.text}"
        data = resp.json()["data"]
        assert len(data["items"]) <= 10
        assert data["page"] == 1
        assert data["page_size"] == 10
        # total 字段默认存在（向后兼容契约）
        assert "total" in data


@pytest.mark.perf
class TestTotalFalseBenefit:
    """``total=false`` 的收益：不执行全量 COUNT，查询数比默认少一。"""

    def test_total_false_skips_count(self, client: TestClient, admin_token: str, seeded_logs: int) -> None:
        headers = auth(admin_token)
        with _capture_statements() as statements:
            client.get(
                "/api/v1/monitor/login-logs",
                params={"page": 1, "page_size": 20},
                headers=headers,
            )
        count_default = sum(1 for sql, _ in statements if "count(" in sql.lower())

        with _capture_statements() as statements:
            client.get(
                "/api/v1/monitor/login-logs",
                params={"page": 1, "page_size": 20, "total": False},
                headers=headers,
            )
        count_false = sum(1 for sql, _ in statements if "count(" in sql.lower())

        assert count_default == 1, f"默认分页应恰好一次 COUNT，实际 {count_default}"
        assert count_false == 0, f"total=false 不应有 COUNT，实际 {count_false}"


@pytest.mark.perf
class TestKeysetBenefit:
    """keyset 深分页收益：游标翻页不做深偏移扫描，且页间无重复/遗漏。"""

    def test_keyset_avoids_deep_offset(self, client: TestClient, admin_token: str, seeded_logs: int) -> None:
        """第 60 页的偏移分页带深 OFFSET，同深度的 keyset 的 OFFSET 恒为 0。"""
        headers = auth(admin_token)
        with _capture_statements() as statements:
            client.get(
                "/api/v1/monitor/login-logs",
                params={"page": 60, "page_size": 20},
                headers=headers,
            )
        offset_stmt = _last_login_log_statement(statements)
        assert offset_stmt is not None, "偏移分页应执行登录日志查询"
        offset_lo = _bound_limit_offset(*offset_stmt)
        assert offset_lo is not None, "偏移分页应带 LIMIT/OFFSET 绑定"
        limit, offset = offset_lo
        assert limit == 20
        assert offset == (60 - 1) * 20, f"第 60 页应深偏移 {1180}，实际 {offset}"

        with _capture_statements() as statements:
            resp = client.get(
                "/api/v1/monitor/login-logs",
                params={"keyset": 1, "page": 60, "page_size": 20},
                headers=headers,
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # keyset 响应不携带 total，携带 next_cursor（契约：total 可选）
        assert "total" not in data
        assert "next_cursor" in data

        keyset_stmt = _last_login_log_statement(statements)
        assert keyset_stmt is not None, "keyset 分页应执行登录日志查询"
        keyset_lo = _bound_limit_offset(*keyset_stmt)
        assert keyset_lo is not None, "keyset 分页应带 LIMIT/OFFSET 绑定"
        k_limit, k_offset = keyset_lo
        assert k_limit == 20
        assert k_offset == 0, f"keyset 分页不应做深偏移扫描，OFFSET 应为 0，实际 {k_offset}"

    def test_keyset_pages_cover_all_rows(self, client: TestClient, admin_token: str, seeded_logs: int) -> None:
        headers = auth(admin_token)
        seen: list[int] = []
        cursor: int | None = None
        pages = 0
        while True:
            params: dict[str, Any] = {"keyset": 1, "page_size": 100}
            if cursor is not None:
                params["cursor"] = cursor
            resp = client.get("/api/v1/monitor/login-logs", params=params, headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            ids = [item["id"] for item in data["items"]]
            assert ids == sorted(ids, reverse=True), "keyset 按 id 降序，页内不应乱序"
            seen.extend(ids)
            pages += 1
            cursor = data.get("next_cursor")
            if cursor is None or not data["items"]:
                break
            assert pages < 200, "游标翻页疑似死循环"
        assert len(seen) == len(set(seen)), "keyset 翻页不应有重复行"
        # 覆盖全部种子行；实际可能略多（admin 登录本身也写登录日志），无重复即无缝隙
        assert len(seen) >= seeded_logs


@pytest.mark.perf
def test_seed_size_sane(client: TestClient, seeded_logs: int) -> None:
    """种子量应足够大到深分页有意义（防御 PERF_ROWS 被误调小）。"""
    assert seeded_logs >= 1000
