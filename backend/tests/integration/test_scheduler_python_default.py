"""P1-12：Python 脚本任务默认关闭，显式开启才能创建/触发。

Python 任务以同权限子进程执行，属高危能力，默认关闭（生产与本地一致）；
只有设置 SCHEDULER_PYTHON_ENABLED=true 才允许创建与触发。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from tests.conftest import auth

API = settings.api_prefix


def test_python_runner_default_off() -> None:
    """字段默认值必须是 False，未显式开启时禁止 Python 任务。"""
    assert Settings.model_fields["scheduler_python_enabled"].default is False


def test_python_job_create_blocked_when_disabled(client: TestClient, admin_token: str, monkeypatch) -> None:
    monkeypatch.setattr(settings, "scheduler_python_enabled", False)
    resp = client.post(
        f"{API}/schedule/jobs",
        headers=auth(admin_token),
        json={
            "name": "被禁脚本",
            "code": "demo.disabled_py",
            "job_type": "python",
            "script_code": "print('no')",
            "trigger_type": "interval",
            "interval_seconds": 3600,
            "timezone": "Asia/Shanghai",
            "args_json": {},
            "status": "paused",
            "timeout_seconds": 30,
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SCHEDULE_PYTHON_DISABLED"
