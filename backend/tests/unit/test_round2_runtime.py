"""第 2 轮运行时核心链路审计的修复回归测试。

Round 1 修的是「静态可读」的断链；本文件钉住只有真正跑起来才暴露的问题：

- TLS 终止代理下 refresh/logout 的同源判定（scheme 不能参与比较）；
- /metrics 的 uptime 混用 wall clock 与 monotonic clock；
- 停机时审计队列只刷一批 / 坏库把 shutdown 卡死；
- 启动中途失败留下半启动单例、调度器 start() 非原子；
- run_job 的 finally 吞掉 CancelledError；
- Redis 初始化失败后 get_cache() 静默降级为内存缓存；
- 清理任务漏掉事件两张表（无限增长）；
- 发布包在 Windows 打包丢 Unix 可执行位、tar.gz 清单与产物脱节。
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.startup as startup
from app.core import csrf, metrics
from app.core.config import settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.jobs.registry import CleanupLogsArgs
from app.kernel.audit import AuditWriteQueue
from app.kernel.models import EventLog, EventOutbox

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# 1. Cookie 接口同源判定
# ---------------------------------------------------------------------------


def _request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/refresh",
        "raw_path": b"/api/v1/auth/refresh",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
        "scheme": "http",  # 反向代理未转发 X-Forwarded-Proto
        "server": ("testserver", 80),
        "client": ("1.2.3.4", 1234),
        "root_path": "",
        "path_params": {},
        "query_params": {},
        "state": {},
    }
    return Request(scope)


def test_csrf_same_host_across_tls_termination() -> None:
    """浏览器发 https://host、应用看到 http://host 时 refresh 不能被 403 打掉。"""
    assert csrf.is_same_origin(_request({"origin": "https://testserver"})) is True
    assert csrf.is_same_origin(_request({"referer": "https://testserver/admin"})) is True
    # 跨站靠 host 阻断，scheme 不参与比较不能变成「任意 scheme 任意站都放行」
    assert csrf.is_same_origin(_request({"origin": "https://evil.example.com"})) is False
    assert csrf.is_same_origin(_request({"origin": "http://testserver.evil.com"})) is False


# ---------------------------------------------------------------------------
# 2. 指标时钟
# ---------------------------------------------------------------------------


def test_metrics_uptime_is_process_uptime() -> None:
    metrics.reset()
    text = metrics.render()
    line = next(item for item in text.splitlines() if item.startswith("fastcms_uptime_seconds "))
    uptime = float(line.split()[1])
    assert 0.0 <= uptime < 5.0, f"uptime 不应是 epoch 量级的常量垃圾值：{uptime}"
    time.sleep(0.02)
    later = float(
        next(i for i in metrics.render().splitlines() if i.startswith("fastcms_uptime_seconds ")).split()[1]
    )
    assert later >= uptime, "uptime 必须随进程运行单调增长"


# ---------------------------------------------------------------------------
# 3. 审计队列停机排空
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, sink: list[Any]) -> None:
        self.sink = sink

    def add(self, row: Any) -> None:
        self.sink.append(row)

    async def commit(self) -> None:
        return None


class _FakeFactory:
    def __init__(self, sink: list[Any]) -> None:
        self.sink = sink

    def __call__(self) -> _FakeFactory:
        return self

    async def __aenter__(self) -> _FakeSession:
        return _FakeSession(self.sink)

    async def __aexit__(self, *_args: Any) -> bool:
        return False


async def test_stop_drains_all_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run 收到停机信号只写完当前批；stop() 必须把剩余全部批次排空。"""
    import app.db.session as session_mod
    import app.kernel.audit as audit_mod

    sink: list[Any] = []
    monkeypatch.setattr(session_mod, "async_session_factory", _FakeFactory(sink))
    monkeypatch.setattr(settings, "audit_batch_size", 2)
    monkeypatch.setattr(audit_mod, "_STOP_DRAIN_SECONDS", 2.0)

    queue = AuditWriteQueue()
    for index in range(7):
        queue._items.append({"action": f"act-{index}"})

    await asyncio.wait_for(queue.stop(), timeout=3)
    assert queue.depth() == 0, "停机不能只写一批就丢掉剩下的审计记录"
    assert len(sink) == 7


async def test_stop_is_bounded_when_flush_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 卡住时 shutdown 必须有上限，不能让 lifespan 的 finally 永不返回。"""
    import app.kernel.audit as audit_mod

    monkeypatch.setattr(audit_mod, "_STOP_DRAIN_SECONDS", 0.05)
    queue = AuditWriteQueue()
    queue._items.append({"action": "stuck"})

    async def hanging_flush() -> int:
        await asyncio.Event().wait()
        return 0

    monkeypatch.setattr(queue, "flush", hanging_flush)
    started = time.monotonic()
    await asyncio.wait_for(queue.stop(), timeout=1.0)
    assert time.monotonic() - started < 0.9, "刷盘卡死必须被超时截断"


# ---------------------------------------------------------------------------
# 4. 启动 / 关闭生命周期
# ---------------------------------------------------------------------------


async def test_shutdown_runs_every_cleanup_even_if_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任一步清理抛异常，其余单例也不能被漏关。"""
    from app.kernel.audit import audit_writer

    calls: list[str] = []

    async def boom() -> None:
        calls.append("scheduler")
        raise RuntimeError("shutdown failed")

    async def audit_stop() -> None:
        calls.append("audit")

    async def cache_close() -> None:
        calls.append("cache")

    class _Engine:
        async def dispose(self) -> None:
            calls.append("engine")

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(startup.scheduler_service, "shutdown", boom)
    monkeypatch.setattr(audit_writer, "stop", audit_stop)
    monkeypatch.setattr(startup, "close_cache", cache_close)
    monkeypatch.setattr(startup, "engine", _Engine())

    await startup._shutdown_resources()
    assert calls == ["scheduler", "audit", "cache", "engine"]


async def test_scheduler_start_rolls_back_on_sync_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """sync_all() 失败不能留下一个「已 firing 但没同步任务」的调度器。"""
    from app.modules.ops.schedule import scheduler as sched

    async def no_recover() -> int:
        return 0

    async def broken_sync() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(sched, "recover_interrupted_logs", no_recover)
    monkeypatch.setattr(sched, "SHUTDOWN_WAIT_SECONDS", 0.1)
    service = sched.SchedulerService()
    monkeypatch.setattr(service, "sync_all", broken_sync)

    with pytest.raises(RuntimeError, match="db down"):
        await service.start()
    assert service.scheduler is None
    assert service.running is False


def test_run_job_broadcast_propagates_cancellation() -> None:
    """finally 里广播 job.finished 时不能吞 CancelledError（3.12 是 BaseException）。"""
    source = (BACKEND / "app" / "jobs" / "runner.py").read_text(encoding="utf-8")
    assert "suppress(Exception, asyncio.CancelledError)" not in source
    tail = source.split('await events.publish(', 1)[1]
    assert "except asyncio.CancelledError:" in tail
    assert "raise" in tail


async def test_cache_init_failure_refuses_silent_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis ping 失败后 get_cache() 不能偷偷造一个 MemoryCache。"""
    import app.core.cache as cache

    closed: list[bool] = []

    class _Client:
        async def ping(self) -> None:
            raise OSError("redis down")

    class _FakeRedis:
        def __init__(self, _url: str) -> None:
            self.client = _Client()

        async def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(cache, "RedisCache", _FakeRedis)
    monkeypatch.setattr(cache, "_cache", None)
    monkeypatch.setattr(cache, "_cache_init_error", None)
    monkeypatch.setattr(settings, "redis_enabled", True)
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")

    with pytest.raises(OSError):
        await cache.init_cache()
    assert closed == [True], "ping 失败时创建的 client 必须被关掉"
    cache._cache = None
    with pytest.raises(RuntimeError, match="拒绝静默降级"):
        cache.get_cache()


# ---------------------------------------------------------------------------
# 5. 事件清理链路
# ---------------------------------------------------------------------------


async def test_cleanup_logs_purges_event_tables_but_keeps_pending_outbox(
    client: TestClient,
) -> None:
    """done/failed 的 outbox 与旧投递记录要清；未派发的 pending 绝不能删。"""
    from app.jobs import builtin

    old = utcnow() - timedelta(days=40)
    async with session_scope() as db:
        db.add(EventLog(event="round2.old", subscriber="s", success=True, created_at=old))
        db.add(EventOutbox(event="round2.old", payload_json="{}", status="done", created_at=old))
        db.add(EventOutbox(event="round2.pending_old", payload_json="{}", status="pending", created_at=old))
        db.add(EventOutbox(event="round2.pending_new", payload_json="{}", status="pending"))

    result = await builtin.cleanup_logs(CleanupLogsArgs(days=7))
    assert "deleted_event_logs=" in result
    assert "deleted_outbox_rows=" in result

    async with session_scope() as db:
        logs = (await db.execute(select(EventLog).where(EventLog.event == "round2.old"))).scalars().all()
        rows = (
            await db.execute(select(EventOutbox).where(EventOutbox.event.like("round2.%")))
        ).scalars().all()
    assert logs == [], "旧的 EventLog 必须被清理"
    codes = {row.event for row in rows}
    assert "round2.old" not in codes, "终态 outbox 必须被清理"
    assert codes == {"round2.pending_old", "round2.pending_new"}, "pending 条目不能被清理任务删掉"


# ---------------------------------------------------------------------------
# 6. 发布产物
# ---------------------------------------------------------------------------


def test_release_zip_keeps_unix_exec_bits(tmp_path: Path) -> None:
    """Windows 打包也要让 ./fastcms / ./install.sh 解包后能直接执行。"""
    import release

    source = ROOT / "fastcms"
    arcname = "fastcms-0.0.0/fastcms"
    target = tmp_path / "probe.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(source, arcname)
        release.apply_zip_mode(bundle, source, arcname)
    with zipfile.ZipFile(target) as bundle:
        info = bundle.getinfo(arcname)
    assert info.create_system == 3
    assert (info.external_attr >> 16) & 0o777 == 0o755


def test_verify_accepts_targz_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--format tar.gz 的产物必须能被 fastcms verify 找到并校验通过。"""
    import fastcms

    dist = tmp_path / "dist-release"
    dist.mkdir()
    archive = dist / "fastcms-0.0.0-20260101-000000.tar.gz"
    archive.write_bytes(b"tarball-bytes")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = dist / "manifest.txt"
    monkeypatch.setattr(fastcms, "ROOT", tmp_path)

    def write_manifest(name: str) -> None:
        manifest.write_text(f"归档：{name}\nSHA256：{digest}\n", encoding="utf-8")

    write_manifest(archive.name)
    assert fastcms._verify_cmd("verify", []) == 0

    write_manifest("fastcms-0.0.0-20260101-000000.zip")
    assert fastcms._verify_cmd("verify", []) == 1, "清单与归档名不符必须判失败"
