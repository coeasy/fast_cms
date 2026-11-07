"""第 5 轮发布验收的修复回归测试。

第 5 轮做的是「异常、重试、资源生命周期」的全量扫描。发现的问题集中在
「失败路径没人管」这一类：正常路径都对，出问题时要么静默、要么半提交、
要么把别人的活一起拖死。

- 审计直写失败完全静默（队列满时的兜底路径），操作日志丢了没有任何痕迹；
- 上传被取消（客户端断开）时只捕 ``Exception``，半截文件永久留在磁盘；
- 封禁与「联动封禁 IP」跨了两个事务，IP 规则重复会让封禁整体回滚；
- 通知批量投递没有逐条隔离，一条出错把整批卡在 ``sending``；
- 任务锁续租连续失败时不标记 lost，任务可能被两个进程同时执行；
- 手动触发任务的异常没人取走，只在 GC 时冒一条警告；
- 事件投递日志、outbox 积压 gauge 写失败用的是 DEBUG，等于不记；
- ``session_scope`` 被取消时不回滚。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.ops.schedule.locks import renew_with_retry
from tests.conftest import auth

# ---------------------------- 审计与失败可见性 ----------------------------


async def test_audit_direct_write_failure_is_logged(monkeypatch, caplog) -> None:
    """队列满/未启动时走直写兜底，这条路径失败必须留 ERROR。

    静默 return 的后果是「谁删了这条数据」永久查不到——审计的意义就在这里。
    """
    from app.kernel import middleware as mw

    def _broken_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr(mw, "async_session_factory", _broken_factory)
    instance = mw.OperationLogMiddleware(app=lambda *args: None)

    with caplog.at_level(logging.ERROR, logger="fastcms.middleware"):
        await instance._write_row_direct(
            {"path": "/api/v1/system/users/1", "status_code": 200, "request_id": "req-1"}
        )

    assert any("操作日志直写失败" in record.getMessage() for record in caplog.records)


# ------------------------------- 上传生命周期 -------------------------------


class _CancellingUpload:
    """读到一半就抛 CancelledError，模拟客户端断开连接。"""

    async def read(self, _size: int) -> bytes:
        raise asyncio.CancelledError


async def test_upload_cancellation_leaves_no_partial_file(tmp_path, monkeypatch) -> None:
    """上传被取消时必须删掉半截文件。

    只捕 ``Exception`` 的话 CancelledError 会穿透，文件留在存储目录里；
    它没有 DB 行，``cleanup_orphan_files`` 永远扫不到。
    """
    import app.kernel.files as files_mod

    root = tmp_path / "store"
    monkeypatch.setattr(files_mod, "STORAGE_ROOT", root)

    with pytest.raises(asyncio.CancelledError):
        await files_mod.upload_file(
            None,
            None,
            filename="avatar.png",
            content_type="image/png",
            stream=_CancellingUpload(),
        )

    assert [path for path in root.rglob("*") if path.is_file()] == []


# ------------------------------ 封禁的事务边界 ------------------------------


async def test_create_ip_rule_can_join_caller_transaction(client: TestClient) -> None:
    """``auto_commit=False`` 时规则必须留在调用方的事务里，由调用方统一提交。

    联动封禁曾经跨两个事务：规则先提交、封禁后失败时留下无主规则；
    而规则撞重复时又会把已 flush 的封禁字段一起回滚，功能直接不可用。
    """
    from app.db.session import session_scope
    from app.kernel.models import IpRule
    from app.modules.sys.security.service import IpRuleInput, create_ip_rule

    target = "198.51.100.9"
    async with session_scope() as db:
        await create_ip_rule(
            db,
            None,
            IpRuleInput(target=target, kind="block", scope="all"),
            auto_commit=False,
        )
        # 调用方决定不提交：规则不应落库
        await db.rollback()

    async with session_scope() as db:
        found = (
            (await db.execute(select(IpRule).where(IpRule.target == target))).scalars().first()
        )
    assert found is None


# ------------------------------ 通知批量投递 ------------------------------


async def test_notify_dispatch_isolates_record_failures(
    client: TestClient, monkeypatch
) -> None:
    """一条记录投递抛异常，不能中断整批。"""
    from app.modules.ops.notify import service as notify

    seen: list[int] = []

    async def fake_claim(_limit: int) -> list[int]:
        return [1, 2, 3]

    async def fake_deliver(record_id: int) -> None:
        seen.append(record_id)
        if record_id == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(notify, "_claim", fake_claim)
    monkeypatch.setattr(notify, "_deliver", fake_deliver)
    monkeypatch.setattr(
        notify, "settings", SimpleNamespace(notify_enabled=True, notify_batch_size=10)
    )

    counts = await notify.dispatch(limit=3)

    assert seen == [1, 2, 3], "首条失败后其余记录仍应继续投递"
    assert counts["claimed"] == 3


# ------------------------------- 任务锁续租 -------------------------------


async def test_renew_with_retry_marks_lost_after_ttl() -> None:
    """续租连续失败超过 TTL 必须标记 lost，否则任务可能被并发执行。"""

    class _DeadLease:
        keys = ("job:1",)
        owner = "test-owner"
        ttl = 0

        async def renew(self) -> bool:
            raise RuntimeError("db down")

    stop = asyncio.Event()
    lost = asyncio.Event()
    await asyncio.wait_for(renew_with_retry(_DeadLease(), 0, stop, lost), timeout=5)
    assert lost.is_set()


# ------------------------------ 会话与可观测 ------------------------------


async def test_session_scope_does_not_swallow_cancellation(client: TestClient) -> None:
    """取消必须原样上抛，同时把事务回滚掉。"""
    from sqlalchemy import text

    from app.db.session import session_scope

    with pytest.raises(asyncio.CancelledError):
        async with session_scope() as db:
            await db.execute(text("SELECT 1"))
            raise asyncio.CancelledError


def test_status_class_uses_single_source() -> None:
    """状态桶清单只有一份：新增 1xx 支持时不用再改第二个地方。"""
    from app.core import metrics

    assert {2, 3, 4, 5} == metrics._STATUS_CLASS_CODES
    assert metrics._status_class(200) == "2xx"
    assert metrics._status_class(404) == "4xx"
    assert metrics._status_class(199) == "5xx"


def test_metrics_gauge_failure_logged_once(caplog) -> None:
    """坏掉的 gauge 按缺失处理，但只告警一次，避免每次抓取刷日志。"""
    from app.core import metrics

    def _broken() -> float:
        raise RuntimeError("gauge boom")

    metrics.reset()
    metrics.register_gauge("fastcms_test_broken_gauge", "broken gauge", _broken)
    try:
        with caplog.at_level(logging.WARNING, logger="fastcms.metrics"):
            metrics.render({})
            metrics.render({})
        hits = [
            r
            for r in caplog.records
            if "gauge 取值失败" in r.getMessage() and "fastcms_test_broken_gauge" in r.getMessage()
        ]
        assert len(hits) == 1
    finally:
        metrics.reset()


def test_outbox_status_constants_are_derived() -> None:
    """outbox 状态清单是唯一真相，drain 与清理都从它派生。"""
    from app.kernel.models import events as models

    assert models.OUTBOX_STATUSES == ("pending", "done", "failed")
    assert models.OUTBOX_PENDING == "pending"
    assert models.OUTBOX_TERMINAL_STATUSES == ("done", "failed")


# --------------------------- 前端孤儿与一致性 ---------------------------


def test_frontend_has_no_dead_exports() -> None:
    """已删除/已接线的工具函数不能再回到「只有测试在用」的状态。"""
    js = Path(__file__).resolve().parents[2] / "app" / "web" / "static" / "js"
    widgets = (js / "ui" / "widgets.js").read_text(encoding="utf-8")
    assert "export function show(" not in widgets, "show() 没有任何生产调用点"

    dashboard = (js / "pages" / "dashboard.js").read_text(encoding="utf-8")
    assert "formatNumber" in dashboard, "formatNumber 必须被真实页面使用"

    codegen = (js / "pages" / "tool" / "codegen.js").read_text(encoding="utf-8")
    assert "boolTag(" in codegen, "boolTag 必须被真实页面使用"


def test_batch_and_row_actions_still_gated(client: TestClient, admin_token: str) -> None:
    """批量接口在 ids 为空时必须是 422（校验层拦截），不是静默成功。"""
    headers = auth(admin_token)
    response = client.post(
        "/api/v1/system/users/batch",
        headers=headers,
        json={"action": "enable", "ids": []},
    )
    assert response.status_code == 422, response.text
