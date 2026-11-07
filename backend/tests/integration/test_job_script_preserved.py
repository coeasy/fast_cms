"""Python 脚本任务编辑不得丢脚本。

回归背景：列表接口不返回 ``script_code``（详情才带，且需要 ``schedule:job:code``）。
页面脚本曾经直接用列表行填编辑表单，保存时把空串写回，等于把已有脚本清空。
修法有两条，这里把两条都钉住：

1. 编辑前取详情，详情在**有权限**时带 ``script_code``；
2. 拿不到脚本时**不传** ``script_code``，后端沿用库里的值（``payload.get`` 语义）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.conftest import auth

API = settings.api_prefix
SCRIPT = "def run(args):\n    return {'ok': True}\n"


def _create_python_job(client: TestClient, headers: dict[str, str]) -> dict:
    resp = client.post(
        f"{API}/schedule/jobs",
        headers=headers,
        json={
            "name": "脚本保留验证",
            "code": "demo.keep_script",
            "job_type": "python",
            "script_code": SCRIPT,
            "trigger_type": "interval",
            "interval_seconds": 3600,
            "timezone": "Asia/Shanghai",
            "args_json": {},
            "status": "paused",
            "timeout_seconds": 30,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_list_omits_script_but_detail_includes_it(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    job = _create_python_job(client, headers)

    listed = client.get(f"{API}/schedule/jobs", headers=headers).json()["data"]["items"]
    row = next(item for item in listed if item["id"] == job["id"])
    # 列表刻意不带脚本：体量大，且需要单独权限。页面必须据此改用详情接口。
    assert "script_code" not in row

    detail = client.get(f"{API}/schedule/jobs/{job['id']}", headers=headers).json()["data"]
    assert detail["script_code"] == SCRIPT


def test_update_without_script_keeps_existing(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    job = _create_python_job(client, headers)

    # 模拟「没拿到脚本」的编辑：载荷里根本不出现 script_code
    updated = client.put(
        f"{API}/schedule/jobs/{job['id']}",
        headers=headers,
        json={
            "name": "改个名字",
            "job_type": "python",
            "trigger_type": "interval",
            "interval_seconds": 1800,
            "status": "paused",
        },
    )
    assert updated.status_code == 200, updated.text
    detail = client.get(f"{API}/schedule/jobs/{job['id']}", headers=headers).json()["data"]
    assert detail["name"] == "改个名字"
    assert detail["script_code"] == SCRIPT, "未传 script_code 时不得清空已有脚本"


def test_update_with_explicit_script_replaces_it(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    job = _create_python_job(client, headers)

    updated = client.put(
        f"{API}/schedule/jobs/{job['id']}",
        headers=headers,
        json={
            "name": "脚本已更新",
            "job_type": "python",
            "script_code": "def run(args):\n    return {'ok': False}\n",
            "trigger_type": "interval",
            "interval_seconds": 1800,
            "status": "paused",
        },
    )
    assert updated.status_code == 200, updated.text
    detail = client.get(f"{API}/schedule/jobs/{job['id']}", headers=headers).json()["data"]
    assert detail["script_code"] == "def run(args):\n    return {'ok': False}\n"
