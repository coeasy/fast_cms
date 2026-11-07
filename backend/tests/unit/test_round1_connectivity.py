"""第 1 轮全链路审计的修复回归测试。

本文件把「静态可读但静态检查抓不到」的断链钉成可执行断言：

- 调度器事件字段（APScheduler 3.x 两类事件不同字段）与后台任务强引用；
- 事务性发件箱必须有真实生产端（全部订阅者失败 → 落 outbox）；
- 审计队列不能因取消丢批次、不能对毒丸批次无限重试、误配批次大小不能空转；
- 分块 JSON 请求体必须有累计上限且原样交还下游；
- 主题列表 / 公开配置必须与前端真正读取的字段对齐；
- 发布包必须带上 gate 的每一个校验脚本与 docs/history；CI 的 tsc 必须装在仓库根。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent, JobSubmissionEvent
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, settings
from app.db.base import utcnow
from app.db.session import session_scope
from app.kernel.audit import AuditWriteQueue
from app.kernel.events import KNOWN_EVENTS
from app.kernel.middleware import _JSON_BODY_LIMIT, _JSON_BODY_MAX_CHUNKS, OperationLogMiddleware
from app.kernel.models import EventOutbox
from tests.conftest import auth

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


# ---------------------------------------------------------------------------
# 调度器 skipped 日志链路
# ---------------------------------------------------------------------------


async def test_submission_event_records_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """MISSED / MAX_INSTANCES 由 JobSubmissionEvent 发出，字段是 scheduled_run_times。

    旧代码读 ``event.run_times``（不存在），异常被 _on_event 吞成 warning，
    skipped 日志整条链路永不落地。这里直接构造真实事件类，断言落库回调被调用。
    """
    from app.modules.ops.schedule import scheduler as sched

    calls: list[tuple[int, Any, str]] = []

    async def fake_log(job_id: int, scheduled_at: Any, reason: str) -> None:
        calls.append((job_id, scheduled_at, reason))

    monkeypatch.setattr(sched, "log_scheduler_skip", fake_log)
    service = sched.SchedulerService()
    planned = utcnow()
    event = JobSubmissionEvent(EVENT_JOB_MISSED, "4242", "default", [planned])

    service._on_event(event)
    tasks = list(sched._skip_tasks)
    assert tasks, "调度器必须持有 Task 强引用，否则事件循环可能提前回收它"
    await asyncio.gather(*tasks)

    assert calls and calls[0][0] == 4242
    assert calls[0][1] == planned
    assert "错过" in calls[0][2]


def test_execution_event_time_field() -> None:
    """JobExecutionEvent 用单数 scheduled_run_time，辅助函数不能只认列表。"""
    from app.modules.ops.schedule.scheduler import _event_scheduled_at

    planned = utcnow()
    event = JobExecutionEvent(1 << 12, "1", "default", planned)
    assert _event_scheduled_at(event) == planned


# ---------------------------------------------------------------------------
# 事务性发件箱：有消费端就必须有生产端
# ---------------------------------------------------------------------------


async def test_failed_publish_enqueues_outbox(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import events

    snapshot = {name: list(events.subscribers(name)) for name in KNOWN_EVENTS}
    monkeypatch.setattr(settings, "app_env", "production")  # 测试环境按约定不落库

    async def boom(**_payload: Any) -> None:
        raise RuntimeError("渠道全挂")

    events.reset()
    events.subscribe("notice.published", boom)
    try:
        assert await events.publish("notice.published", notice_id=99) == 0
        async with session_scope() as db:
            rows = (
                await db.execute(select(EventOutbox).where(EventOutbox.event == "notice.published"))
            ).scalars().all()
        assert any(row.status == "pending" for row in rows), "零成功投递必须进发件箱等待重试"
    finally:
        events.reset()
        for name, handlers in snapshot.items():
            for handler in handlers:
                events._handlers[name].append(handler)


# ---------------------------------------------------------------------------
# 审计队列：取消不丢、重试有界、误配不空转
# ---------------------------------------------------------------------------


def _audit_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "user_id": None,
        "username": "tester",
        "module": "sys",
        "action": "round1",
        "method": "POST",
        "path": "/api/v1/round1",
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


async def test_flush_requeues_batch_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """取消命中 await commit() 时，已出队的整批记录必须回到队头。"""
    import app.db.session as session_mod

    class _Session:
        def add(self, _row: Any) -> None:
            return None

        async def commit(self) -> None:
            raise asyncio.CancelledError()

    class _Factory:
        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> _Session:
            return _Session()

        async def __aexit__(self, *_args: Any) -> bool:
            return False

    monkeypatch.setattr(session_mod, "async_session_factory", _Factory())
    queue = AuditWriteQueue()
    queue._items.append(_audit_row())

    with pytest.raises(asyncio.CancelledError):
        await queue.flush()
    assert queue.depth() == 1, "取消不能把这一批审计记录弄丢"


async def test_run_bounds_retries_and_drops_poison_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """持续失败不能每 200ms 无限重试同一批：达到上限丢弃并重置计数。"""
    monkeypatch.setattr(settings, "audit_max_retries", 2)
    monkeypatch.setattr(settings, "audit_flush_interval_seconds", 0.01)

    queue = AuditWriteQueue()
    queue._items.append(_audit_row())
    queue._last_batch_size = 1
    calls = 0

    async def failing_flush() -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("db down")

    monkeypatch.setattr(queue, "flush", failing_flush)
    task = asyncio.create_task(queue._run())
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if calls >= 2 and queue.depth() == 0:
                break
        assert calls >= 2
        assert queue.depth() == 0, "毒丸批次必须被丢弃，否则后台任务永远出不去"
    finally:
        queue._stop.set()
        await asyncio.wait_for(task, timeout=2)


async def test_zero_batch_size_still_drains(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIT_BATCH_SIZE<=0 会让 while 永不 pop；必须钳到 1。"""
    monkeypatch.setattr(settings, "audit_batch_size", 0)
    queue = AuditWriteQueue()
    queue._items.append(_audit_row())
    assert await queue.flush() == 1
    assert queue.depth() == 0


# ---------------------------------------------------------------------------
# 分块请求体：累计上限 + 原样交还下游
# ---------------------------------------------------------------------------


async def test_capture_body_bounds_endless_chunked_stream() -> None:
    class EndlessReceive:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self) -> dict[str, Any]:
            self.calls += 1
            return {"type": "http.request", "body": b"x" * 100, "more_body": True}

    scope: dict[str, Any] = {
        "type": "http",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
    }
    receive = EndlessReceive()
    detail, replay = await OperationLogMiddleware._capture_body(None, scope, receive)  # type: ignore[arg-type]

    assert detail is None, "超限请求体不应被记进审计明细"
    assert receive.calls <= _JSON_BODY_MAX_CHUNKS
    first = await replay()
    assert first["more_body"] is True
    assert len(first["body"]) <= _JSON_BODY_LIMIT + 100
    # 剩余字节继续由原始 receive 提供，下游看到的流没有丢字节
    assert (await replay())["more_body"] is True


# ---------------------------------------------------------------------------
# 前后端字段契约
# ---------------------------------------------------------------------------


def test_theme_list_exposes_is_active(client: TestClient, admin_token: str) -> None:
    """theme.js 用 themes.find(i => i.is_active) 定位当前主题，列表必须真给这个字段。"""
    response = client.get("/api/v1/system/themes", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    items = response.json()["data"]
    assert items
    assert all(isinstance(item["is_active"], bool) for item in items)
    assert sum(1 for item in items if item["is_active"]) == 1
    active = next(item for item in items if item["is_active"])
    public = client.get("/api/v1/system/themes/public").json()["data"]
    assert public["active"]["id"] == active["id"]


def test_public_config_exposes_both_descriptions(client: TestClient) -> None:
    """portal.js 读 system.description，auth.js 读 system.login_description。"""
    data = client.get("/api/v1/system/public-config").json()["data"]
    assert data.get("system.description"), "前台 hero 标题读不到就永远退回模板硬编码"
    assert data.get("system.login_description"), "登录页描述是公开参数，必须能被前端读到"


# ---------------------------------------------------------------------------
# 发布包 / CI / 配置优先级
# ---------------------------------------------------------------------------


def test_release_package_ships_every_gate_script_and_history_docs() -> None:
    import gate
    import release

    needed: set[str] = set()
    for steps in gate.GROUPS.values():
        for _label, cmd, _cwd in steps:
            for arg in cmd[1:]:
                text = str(arg)
                if text.endswith(".py") and "scripts" in text:
                    needed.add(Path(text).name if Path(text).is_absolute() else Path(text).as_posix())
    missing = {name for name in needed if f"scripts/{Path(name).name}" not in release.INCLUDE_FILES}
    assert not missing, f"发布包缺门禁脚本：{sorted(missing)}"

    history = {f"docs/history/{path.name}" for path in (ROOT / "docs" / "history").glob("*.md")}
    assert history, "docs/history 为空，测试前提不成立"
    assert history <= set(release.INCLUDE_DOCS), "发布包漏了 docs/README.md 索引的归档文档"


def test_ci_installs_typescript_at_repo_root() -> None:
    """gate 在 ROOT/node_modules 找 tsc，CI 的 npm 步骤必须覆盖 backend 默认 cwd。"""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "npm install --no-save typescript" in line:
            block = "\n".join(lines[max(0, index - 4) : index])
            assert "working-directory: ." in block, "CI 的 tsc 装错目录会让类型门禁静默跳过"
            break
    else:
        pytest.fail("CI 没有安装 typescript 的步骤")


def test_ci_gate_fails_when_tsc_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI 里缺 typescript 必须硬失败，不能沿用本地的「跳过并提示」。"""
    import gate

    monkeypatch.setattr(gate, "_TSC", None)
    monkeypatch.setenv("CI", "true")
    assert gate.run(["lint"]) == 1


def test_root_dotenv_wins_over_backend_dotenv(tmp_path: Path) -> None:
    """文档与脚本都只认仓库根 .env；pydantic-settings 的后读文件覆盖先读文件。"""
    source = (BACKEND / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert ('env_file=(str(BASE_DIR / ".env"), str(BASE_DIR.parent / ".env"))') in source

    backend_env = tmp_path / "backend.env"
    root_env = tmp_path / "root.env"
    backend_env.write_text("APP_NAME=FROM_BACKEND\n", encoding="utf-8")
    root_env.write_text("APP_NAME=FROM_ROOT\n", encoding="utf-8")
    resolved = Settings(_env_file=(str(backend_env), str(root_env)))
    assert resolved.app_name == "FROM_ROOT"
