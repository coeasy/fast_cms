"""P1-1：任务编码语义契约。

文档（docs/定时任务.md）与实现约定：
- 任务 `code` 本身不要求唯一 —— 多个任务复用同一 handler 属于合法配置；
- 但 **Python 脚本任务**以 `code` 为唯一身份，重复创建必须 409（服务层预检，
  数据库层不设全局唯一约束，因为 registry 任务需要允许重复）。

本文件把这些约定固化为回归测试，确保模型 / 迁移 / 服务 / 文档四者不再漂移。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.conftest import auth

API = settings.api_prefix


def _create(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    code: str,
    job_type: str = "registry",
) -> object:
    payload: dict = {
        "name": name,
        "code": code,
        "trigger_type": "interval",
        "interval_seconds": 3600,
        "timezone": "Asia/Shanghai",
        "args_json": {},
        "status": "paused",
    }
    if job_type == "python":
        payload["job_type"] = "python"
        payload["script_code"] = "print('ok')"
        payload["timeout_seconds"] = 30
    return client.post(f"{API}/schedule/jobs", headers=headers, json=payload)


def test_registry_job_duplicate_code_allowed(client: TestClient, admin_token: str) -> None:
    """多个任务复用同一 handler code 是合法配置，数据库不设唯一约束。"""
    headers = auth(admin_token)
    first = _create(client, headers, "心跳副本A", "system.heartbeat")
    assert first.status_code == 200, first.text
    second = _create(client, headers, "心跳副本B", "system.heartbeat")
    assert second.status_code == 200, second.text
    first_id = first.json()["data"]["id"]
    second_id = second.json()["data"]["id"]
    try:
        assert first_id != second_id
        assert first.json()["data"]["code"] == second.json()["data"]["code"]
    finally:
        assert client.delete(f"{API}/schedule/jobs/{first_id}", headers=headers).status_code == 200
        assert client.delete(f"{API}/schedule/jobs/{second_id}", headers=headers).status_code == 200


def test_python_job_duplicate_code_rejected(client: TestClient, admin_token: str) -> None:
    """Python 脚本任务以 code 为唯一身份，重复必须 409 且错误码明确。"""
    headers = auth(admin_token)
    first = _create(client, headers, "脚本A", "demo.dup_py", job_type="python")
    assert first.status_code == 200, first.text
    first_id = first.json()["data"]["id"]
    try:
        dup = _create(client, headers, "脚本B", "demo.dup_py", job_type="python")
        assert dup.status_code == 409, dup.text
        assert dup.json()["code"] == "SCHEDULE_JOB_CODE_EXISTS"
    finally:
        assert client.delete(f"{API}/schedule/jobs/{first_id}", headers=headers).status_code == 200
