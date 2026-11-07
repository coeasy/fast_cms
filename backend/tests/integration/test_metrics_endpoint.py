"""B-8 / 4-2：``/metrics`` 端点注册（METRICS_ENABLED）集成测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app


def test_metrics_enabled_returns_prometheus_text(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", True)
    scoped = TestClient(create_app())
    try:
        resp = scoped.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "fastcms_uptime_seconds" in resp.text
        assert "# TYPE fastcms_http_requests_total counter" in resp.text
        assert resp.headers.get("cache-control") == "no-store"
    finally:
        scoped.close()


def test_metrics_disabled_returns_404(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", False)
    scoped = TestClient(create_app())
    try:
        resp = scoped.get("/metrics")
        assert resp.status_code == 404
    finally:
        scoped.close()


def test_request_is_counted_after_hit(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "metrics_enabled", True)
    scoped = TestClient(create_app())
    try:
        # 先打一个请求，让计数 > 0，再抓取
        assert scoped.get("/healthz").json()["status"] == "ok"
        resp = scoped.get("/metrics")
        assert 'method="GET",status="2xx"' in resp.text
    finally:
        scoped.close()
