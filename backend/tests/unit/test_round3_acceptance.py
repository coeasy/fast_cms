"""第 3 轮发布验收的修复回归测试。

第 3 轮发现的是「前两轮的修复本身不够彻底」以及「门禁有盲区」两类问题：

- 前台公告列表/正文把 api.get 已解开的信封又解了一层（真实死链，门禁查不到）；
- 连通性门禁既没把 portal/ 当语料，也不检查响应形状；
- CSRF 放宽 scheme 时把降级来源也放行了、畸形 Origin 会抛未捕获 ValueError；
- close_cache 非原子、shutdown 被取消会留下 firing 的调度器、
  resync_timezone 重建失败会让调度器永久失联；
- tar.gz 转换二次丢失可执行位、verify 在多格式共存时误判失败；
- 口令拒绝采样没有上限。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tarfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Request
from fastapi.testclient import TestClient

from app.core import csrf

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
JS_DIR = BACKEND / "app" / "web" / "static" / "js"
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# 1. 前台公告页面 / 门禁盲区
# ---------------------------------------------------------------------------


def _request(headers: dict[str, str], scheme: str = "http") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/refresh",
        "raw_path": b"/api/v1/auth/refresh",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
        "scheme": scheme,
        "server": ("testserver", 443 if scheme == "https" else 80),
        "client": ("1.2.3.4", 1234),
        "root_path": "",
        "path_params": {},
        "query_params": {},
        "state": {},
    }
    return Request(scope)


def test_portal_pages_consume_unwrapped_payload() -> None:
    """api.get 返回的是 body.data，页面不能再读一层 .data。"""
    for name in ("content-list.js", "content-detail.js"):
        source = (JS_DIR / "portal" / name).read_text(encoding="utf-8")
        assert "?.data" not in source, f"{name} 仍在重复解信封"
    listing = (JS_DIR / "portal" / "content-list.js").read_text(encoding="utf-8")
    detail = (JS_DIR / "portal" / "content-detail.js").read_text(encoding="utf-8")
    assert "render(data.items || [])" in listing
    assert "if (!item || !item.id)" in detail


def test_published_notice_envelope_shape(client: TestClient) -> None:
    """后端确实只包一层信封，且公开可读（前台匿名拉取）。"""
    response = client.get("/api/v1/content/notices/published")
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert "items" in payload and "total" in payload
    assert "data" not in payload


def test_coverage_gate_covers_portal_and_response_shape() -> None:
    import check_api_coverage as coverage

    assert JS_DIR / "portal" in coverage.CORPUS_DIRS
    assert coverage.DOUBLE_UNWRAP_RE.search("const data = body?.data || {};")
    assert coverage.DOUBLE_UNWRAP_RE.search("const x = result.data;")
    assert not coverage.DOUBLE_UNWRAP_RE.search("button.closest('tr')?.dataset.id")
    assert coverage.check_envelope_unwrap() == []


# ---------------------------------------------------------------------------
# 2. CSRF 同源判定收敛
# ---------------------------------------------------------------------------


def test_csrf_allows_only_upgrade_to_https() -> None:
    """TLS 终止：浏览器 https、应用 http 放行；反向降级不放行。"""
    assert csrf.is_same_origin(_request({"origin": "https://testserver"})) is True
    assert csrf.is_same_origin(_request({"origin": "http://testserver"}, scheme="https")) is False
    assert csrf.is_same_origin(_request({"origin": "https://testserver"}, scheme="https")) is True
    assert csrf.is_same_origin(_request({"origin": "https://evil.example.com"})) is False


def test_csrf_malformed_origin_is_denied_not_500() -> None:
    """畸形 IPv6 / 非法 scheme 的 Origin 不能抛 ValueError，也不能当作无 Origin 放行。"""
    for raw in ("http://[::1", "null", "//testserver", "javascript://testserver"):
        assert csrf.request_origin(_request({"origin": raw})) is None
        assert csrf.is_same_origin(_request({"origin": raw})) is False
    assert csrf.is_same_origin(_request({})) is True


# ---------------------------------------------------------------------------
# 3. 生命周期原子性
# ---------------------------------------------------------------------------


async def test_close_cache_detaches_on_failure_and_keeps_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.cache as cache

    class Broken:
        async def close(self) -> None:
            raise OSError("close failed")

    monkeypatch.setattr(cache, "_cache", Broken())
    monkeypatch.setattr(cache, "_cache_init_error", "Redis 连接失败")
    with pytest.raises(OSError):
        await cache.close_cache()
    assert cache._cache is None, "半关闭的 client 不能继续挂在单例上"
    assert cache._cache_init_error == "Redis 连接失败", "启动失败标记不能被 shutdown 抹掉"


async def test_shutdown_closes_scheduler_even_when_wait_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.ops.schedule import scheduler as sched

    class FakeScheduler:
        running = True

        def __init__(self) -> None:
            self.closed = False
            self._executors: dict[str, Any] = {}

        def pause(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            self.closed = True

    fake = FakeScheduler()
    service = sched.SchedulerService()
    service.scheduler = fake  # type: ignore[assignment]
    blocker = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(sched, "_inflight_job_tasks", lambda _s: {blocker})

    async def cancelled_wait(*_args: Any, **_kwargs: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "wait", cancelled_wait)
    try:
        with pytest.raises(asyncio.CancelledError):
            await service.shutdown()
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker
    assert fake.closed is True, "finally 里必须关掉调度器"
    assert service.scheduler is None


async def test_resync_timezone_restores_old_scheduler_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.ops.schedule import scheduler as sched

    old = AsyncIOScheduler(timezone=ZoneInfo("Asia/Tokyo"))
    old.start()
    service = sched.SchedulerService()
    service.scheduler = old
    monkeypatch.setattr(sched, "scheduler_timezone", lambda: "UTC")

    async def broken_sync() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "sync_all", broken_sync)
    try:
        with pytest.raises(RuntimeError, match="db down"):
            await service.resync_timezone()
        assert service.scheduler is old, "重建失败不能让调度器变成 None 后永久失联"
        assert old.running is True
    finally:
        old.shutdown(wait=False)
        service.scheduler = None


def test_shutdown_resources_covers_all_four_cleanups() -> None:
    """_shutdown_resources 存在且覆盖四个清理步骤（早退的回归护栏）。"""
    source = (BACKEND / "app" / "startup.py").read_text(encoding="utf-8")
    for name in ("scheduler_service.shutdown", "audit_writer.stop", "close_cache", "engine.dispose"):
        assert name in source


# ---------------------------------------------------------------------------
# 4. 发布产物
# ---------------------------------------------------------------------------


def test_tar_mode_filter_restores_exec_bits() -> None:
    import release

    for name in release.EXECUTABLE_FILES:
        info = tarfile.TarInfo(f"fastcms-0.1.0/{name}")
        info.mode = 0o644
        assert release.normalize_tar_mode(info).mode == 0o755
    plain = tarfile.TarInfo("fastcms-0.1.0/README.md")
    plain.mode = 0o644
    assert release.normalize_tar_mode(plain).mode == 0o644


def test_verify_finds_manifest_archive_among_multiple_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目录里同时存在历史 zip 与新 tar.gz 时，按清单挑对归档而不是只看 mtime。"""
    import fastcms

    dist = tmp_path / "dist-release"
    dist.mkdir()
    older = dist / "fastcms-0.1.0-20260101-000000.zip"
    older.write_bytes(b"older-zip-bytes")
    newer = dist / "fastcms-0.1.0-20260101-000100.tar.gz"
    newer.write_bytes(b"newer-tarball-bytes")
    os.utime(newer, (newer.stat().st_mtime + 10, newer.stat().st_mtime + 10))
    (dist / "manifest.txt").write_text(
        f"归档：{older.name}\nSHA256：{hashlib.sha256(older.read_bytes()).hexdigest()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fastcms, "ROOT", tmp_path)
    assert fastcms._verify_cmd("verify", []) == 0


def test_password_samplers_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """字母表被改成全字母时不能无限采样。"""
    import install

    import app.cli as cli

    # cli.secrets 与 install.secrets 是同一个模块对象，改一次对两处生效
    monkeypatch.setattr(cli.secrets, "choice", lambda _alphabet: "A")
    result = cli._generated_password(16)
    assert len(result) == 16
    assert any(c.isdigit() for c in result)
    assert any(c.isdigit() for c in install.generate_password(16))


# ---------------------------------------------------------------------------
# 5. 最终发布产物 / 门禁盲区补齐（第 3 轮审计新增）
# ---------------------------------------------------------------------------


def test_release_ships_tsconfig() -> None:
    """gate.py 的 frontend-tsc 读仓库根 tsconfig.json，发布包必须带上它。"""
    import release

    assert "tsconfig.json" in release.INCLUDE_FILES


def test_docs_gate_validates_markdown_links() -> None:
    """check_docs 要能识别并解析 Markdown 相对链接，而不是只校验反引号路径。"""
    import check_docs

    assert check_docs.md_link_target("docs/history/x.md") == "docs/history/x.md"
    assert check_docs.md_link_target("file.md#section") == "file.md"
    assert check_docs.md_link_target("https://example.com/a.md") is None
    assert check_docs.md_link_target("#anchor") is None
    assert check_docs.md_link_target("not-a-doc.txt") is None
    assert check_docs.scan()[0] == [], "文档里不应再有指向不存在文件的链接"


def test_docker_honors_data_dir() -> None:
    """DB_URL / STORAGE_LOCAL_DIR 留空由 DATA_DIR 推导，卷挂载点跟随 DATA_DIR。"""
    compose = (ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "DB_URL: ${DB_URL:-}" in compose
    assert "STORAGE_LOCAL_DIR: ${STORAGE_LOCAL_DIR:-}" in compose
    assert "${DATA_DIR:-/app/backend/data}" in compose
    assert "DB_URL=sqlite+aiosqlite" not in dockerfile
    assert "STORAGE_LOCAL_DIR=/app/backend/data" not in dockerfile
    assert "${DATA_DIR:-/app/backend/data}/initial_admin_password.txt" in entrypoint


def test_db_check_is_read_only() -> None:
    """db check 不能走 ensure_database：dev 下那会悄悄升级数据库（写副作用）。"""
    cli_source = (BACKEND / "app" / "cli.py").read_text(encoding="utf-8")
    migrate_source = (BACKEND / "app" / "db" / "migrate.py").read_text(encoding="utf-8")
    db_body = cli_source.split("async def _db", 1)[1].split("async def _seed", 1)[0]
    assert "check_database" in db_body
    assert "await ensure_database()" not in db_body
    assert "async def check_database" in migrate_source
