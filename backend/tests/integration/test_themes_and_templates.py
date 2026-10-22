from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.conftest import auth


def _wait_for_log(client: TestClient, headers: dict, job_id: int, statuses: set[str]) -> dict:
    for _ in range(80):
        logs = client.get(
            "/api/v1/schedule/job-logs",
            headers=headers,
            params={"job_id": job_id},
        ).json()["data"]["items"]
        matched = next((item for item in logs if item["status"] in statuses), None)
        if matched is not None:
            return matched
        time.sleep(0.1)
    raise AssertionError(f"等待任务日志状态 {statuses} 超时")


def test_public_and_custom_themes(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    public = client.get("/api/v1/system/themes/public")
    assert public.status_code == 200, public.text
    payload = public.json()["data"]
    assert payload["active"]["code"] == "classic-blue"
    assert {item["code"] for item in payload["items"]} >= {
        "classic-blue",
        "dark-night",
        "emerald-green",
        "violet-dream",
        "ink-compact",
        "finance-dark",
        "finance-light",
    }
    assert "tdx-finance-dark" not in {item["code"] for item in payload["items"]}
    finance = next(item for item in payload["items"] if item["code"] == "finance-dark")
    assert finance["name"] == "金融深色紧凑"
    assert finance["config"]["colorBgLayout"] == "#0a0f18"
    assert finance["config"]["colorBgSider"] == "#0b1220"
    assert finance["config"]["darkAlgorithm"] is True
    # 紧凑算法会把字号再降一档，金融模板改用显式字号 + 表格密度开关
    assert finance["config"]["compactAlgorithm"] is False
    assert finance["config"]["fontSize"] == 13
    assert finance["config"]["componentSize"] == "small"
    assert finance["config"]["tableCompact"] is True
    assert finance["config"]["siderWidth"] == 184
    light = next(item for item in payload["items"] if item["code"] == "finance-light")
    assert light["config"]["colorBgLayout"] == "#f2f4f8"
    assert light["config"]["darkAlgorithm"] is False
    assert light["config"]["colorBgSider"] == "#ffffff"
    assert light["config"]["contentPadding"] == 16

    created = client.post(
        "/api/v1/system/themes",
        headers=headers,
        json={
            "code": "test-sunset",
            "name": "测试主题",
            "description": "集成测试主题",
            "config": {"colorPrimary": "#ff7a45", "navTheme": "light"},
            "sort": 99,
        },
    )
    assert created.status_code == 200, created.text
    theme_id = created.json()["data"]["id"]

    builtin = client.get("/api/v1/system/themes", headers=headers).json()["data"][0]
    rejected = client.put(
        f"/api/v1/system/themes/{builtin['id']}",
        headers=headers,
        json={"name": "不可修改"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "THEME_BUILTIN"

    activated = client.post(
        f"/api/v1/system/themes/{theme_id}/activate", headers=headers
    )
    assert activated.status_code == 200, activated.text
    active = client.get("/api/v1/system/themes/public").json()["data"]["active"]
    assert active["code"] == "test-sunset"
    config = client.get("/api/v1/system/public-config").json()["data"]
    assert config["system.theme"] == "test-sunset"

    delete_active = client.delete(f"/api/v1/system/themes/{theme_id}", headers=headers)
    assert delete_active.status_code == 409

    classic = next(
        item
        for item in client.get("/api/v1/system/themes", headers=headers).json()["data"]
        if item["code"] == "classic-blue"
    )
    client.post(f"/api/v1/system/themes/{classic['id']}/activate", headers=headers)
    deleted = client.delete(f"/api/v1/system/themes/{theme_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text


def test_theme_extended_tokens_round_trip(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    created = client.post(
        "/api/v1/system/themes",
        headers=headers,
        json={
            "code": "test-bg-token",
            "name": "扩展令牌主题",
            "config": {
                "colorPrimary": "#1e88ff",
                "colorBgLayout": "#0a0f18",
                "colorBgContainer": "#111a2b",
                "colorBgSider": "#0b1220",
                "colorBgHeader": "#0b1220",
                "colorLink": "#4aa3ff",
                "fontSize": 13,
                "controlHeight": 28,
                "componentSize": "small",
                "contentPadding": 16,
                "siderWidth": 184,
                "showBreadcrumb": False,
                "tableHeaderBg": "#16233a",
                "tableCompact": True,
                "watermark": "内部系统",
            },
        },
    )
    assert created.status_code == 200, created.text
    theme_id = created.json()["data"]["id"]
    config = created.json()["data"]["config"]
    assert config["colorBgSider"] == "#0b1220"
    assert config["colorLink"] == "#4aa3ff"
    assert config["fontSize"] == 13
    assert config["controlHeight"] == 28
    assert config["componentSize"] == "small"
    assert config["contentPadding"] == 16
    assert config["siderWidth"] == 184
    assert config["showBreadcrumb"] is False
    assert config["tableHeaderBg"] == "#16233a"
    assert config["tableCompact"] is True
    assert config["watermark"] == "内部系统"

    updated = client.put(
        f"/api/v1/system/themes/{theme_id}",
        headers=headers,
        json={"config": {"colorPrimary": "#1e88ff", "colorBgLayout": "#f2f4f8"}},
    )
    assert updated.status_code == 200, updated.text
    restored = updated.json()["data"]["config"]
    assert restored["colorBgSider"] is None
    assert restored["fontSize"] == 14
    assert restored["componentSize"] == "middle"
    assert restored["showBreadcrumb"] is True
    assert restored["tableHeaderBg"] is None
    assert restored["tableCompact"] is False
    assert restored["watermark"] == ""

    rejected = client.post(
        "/api/v1/system/themes",
        headers=headers,
        json={
            "code": "test-bg-bad",
            "name": "非法背景色",
            "config": {"colorBgLayout": "red"},
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "VALIDATION_ERROR"

    out_of_range = client.post(
        "/api/v1/system/themes",
        headers=headers,
        json={"code": "test-font-bad", "name": "非法字号", "config": {"fontSize": 99}},
    )
    assert out_of_range.status_code == 422

    deleted = client.delete(f"/api/v1/system/themes/{theme_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text


def test_python_template_and_duplicate_execution(client: TestClient, admin_token: str) -> None:
    headers = auth(admin_token)
    templates = client.get("/api/v1/schedule/templates", headers=headers)
    assert templates.status_code == 200, templates.text
    preset = next(
        item
        for item in templates.json()["data"]
        if item["code"] == "preset.python_healthcheck"
    )
    assert preset["job_type"] == "python"
    assert "FASTCMS_JOB_ARGS" in preset["script_code"]

    invalid = client.post(
        "/api/v1/schedule/python/validate",
        headers=headers,
        json={"script_code": "def broken(:\n    pass"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "SCHEDULE_SCRIPT_INVALID"

    script = (
        "import json\n"
        "import os\n"
        "import time\n"
        "args = json.loads(os.environ.get('FASTCMS_JOB_ARGS', '{}'))\n"
        "print('start', args['message'], flush=True)\n"
        "time.sleep(2)\n"
        "print('done')\n"
    )
    valid = client.post(
        "/api/v1/schedule/python/validate",
        headers=headers,
        json={"script_code": script},
    )
    assert valid.status_code == 200, valid.text

    created = client.post(
        "/api/v1/schedule/templates",
        headers=headers,
        json={
            "code": "test.python_sleep",
            "name": "测试 Python 模板",
            "job_type": "python",
            "script_code": script,
            "args_json": {"message": "template"},
            "trigger_type": "interval",
            "interval_seconds": 600,
            "timeout_seconds": 30,
        },
    )
    assert created.status_code == 200, created.text
    template_id = created.json()["data"]["id"]

    job = client.post(
        "/api/v1/schedule/jobs",
        headers=headers,
        json={
            "name": "测试 Python 任务",
            "code": "test.python_sleep",
            "job_type": "python",
            "template_id": template_id,
            "trigger_type": "interval",
            "interval_seconds": 600,
            "status": "paused",
        },
    )
    assert job.status_code == 200, job.text
    job_data = job.json()["data"]
    job_id = job_data["id"]
    assert job_data["job_type"] == "python"
    assert job_data["template_id"] == template_id
    assert "time.sleep(2)" in job_data["script_code"]

    first = client.post(f"/api/v1/schedule/jobs/{job_id}/trigger", headers=headers)
    assert first.status_code == 200, first.text
    running = _wait_for_log(client, headers, job_id, {"running"})

    second = client.post(f"/api/v1/schedule/jobs/{job_id}/trigger", headers=headers)
    assert second.status_code == 200, second.text
    skipped = _wait_for_log(client, headers, job_id, {"skipped"})
    skipped_detail = client.get(
        f"/api/v1/schedule/job-logs/{skipped['id']}", headers=headers
    ).json()["data"]
    assert "已有同一任务在执行" in skipped_detail["error"]

    detail = client.get(
        f"/api/v1/schedule/job-logs/{running['id']}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["job_type"] == "python"
    assert detail.json()["data"]["code_hash"]

    success = _wait_for_log(client, headers, job_id, {"success", "failed"})
    final = client.get(f"/api/v1/schedule/job-logs/{success['id']}", headers=headers).json()["data"]
    assert final["status"] == "success", final
    assert "start template" in (final["output"] or "")
    assert "done" in (final["output"] or "")

    removed = client.delete(f"/api/v1/schedule/jobs/{job_id}", headers=headers)
    assert removed.status_code == 200, removed.text
    removed_template = client.delete(
        f"/api/v1/schedule/templates/{template_id}", headers=headers
    )
    assert removed_template.status_code == 200, removed_template.text
