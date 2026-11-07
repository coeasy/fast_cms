"""B-1 审计日志单写断言（Phase 0-4 红测试 / Phase 1 转绿）。

问题：``OperationLogMiddleware`` 对所有变更类请求写一条操作日志，但 ``sys/security``
模块的路径 ``/security/**`` 不在中间件排除前缀里，同时其 service 层 ``_audit()``
又写一条 —— 一次 ``POST /api/v1/security/ip-rules`` 会产生 **2 条**操作日志。

本测试先对每次变更请求断言「恰好新增 1 条」，再断言这条日志的 ``duration_ms``
是真实计时（> 0），作为 Phase 1（谁写谁负责 + 真实耗时）的验收基线。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.conftest import current_db_path


def _new_log_rows(db_path: Path, max_id_before: int) -> list[tuple[int, int]]:
    """返回 (id, duration_ms) 列表，按 id 升序。"""
    with sqlite3.connect(db_path.as_posix()) as conn:
        rows = conn.execute(
            "SELECT id, duration_ms FROM sys_operation_log WHERE id > ? ORDER BY id",
            (max_id_before,),
        ).fetchall()
        return [(int(r[0]), int(r[1])) for r in rows]


def _max_log_id(db_path: Path) -> int:
    with sqlite3.connect(db_path.as_posix()) as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM sys_operation_log").fetchone()
        return int(row[0])


def _security_ip_rule_create_payload() -> dict:
    return {"target": "198.51.100.7", "kind": "block", "scope": "all", "reason": "B-1 审计单写测试"}


def test_security_mutation_writes_exactly_one_operation_log(client, admin_token) -> None:
    db_path = current_db_path()
    max_before = _max_log_id(db_path)

    resp = client.post(
        "/api/v1/security/ip-rules",
        json=_security_ip_rule_create_payload(),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text

    new_rows = _new_log_rows(db_path, max_before)
    assert len(new_rows) == 1, (
        f"一次变更请求应恰好新增 1 条操作日志，实际 {len(new_rows)} 条"
        "（B-1：security 模块被中间件与 service 层双写）"
    )
    # B-1 ②：这条日志的耗时必须是真实计时（中间件写 real elapsed；service 层若
    # 硬编码 duration_ms=0 会在这条断言上失败）。
    _, duration_ms = new_rows[0]
    assert duration_ms > 0, (
        f"操作日志 duration_ms 应为真实耗时，实际为 {duration_ms}（B-1 ②：硬编码 0）"
    )
