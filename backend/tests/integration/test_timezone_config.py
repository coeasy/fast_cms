from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.core.tz import current_timezone
from tests.conftest import auth

TIMEZONE_KEY = "system.timezone"


def _unwrap(response) -> Any:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _timezone_config(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    rows = _unwrap(client.get("/api/v1/system/configs", params={"keyword": TIMEZONE_KEY}, headers=headers))
    matched = [row for row in rows if row["key"] == TIMEZONE_KEY]
    assert matched, f"基线数据缺少 {TIMEZONE_KEY} 参数"
    return matched[0]


def _visible_timezone(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    """公开配置与元信息接口是前端取时区的两条路径，这里一起断言。"""
    return (
        _unwrap(client.get("/api/v1/system/public-config"))[TIMEZONE_KEY],
        _unwrap(client.get("/api/v1/meta/version"))["timezone"],
    )


def test_timezone_config_switches_runtime_and_resets_back(client: TestClient, admin_token: str) -> None:
    """系统时区是运行期状态：改参数要立刻反映到接口，测试结束必须还原，否则同批用例互相污染。"""
    headers = auth(admin_token)
    original = _timezone_config(client, headers)
    assert original["value"] == "Asia/Shanghai"
    assert current_timezone() == "Asia/Shanghai"

    try:
        assert client.put(f"/api/v1/system/configs/{original['id']}", headers=headers, json={"value": "UTC"}).status_code == 200
        assert current_timezone() == "UTC"
        assert _visible_timezone(client, headers) == ("UTC", "UTC")
        runtime = _unwrap(client.get("/api/v1/monitor/server", headers=headers))["runtime"]
        # SCHEDULER_TIMEZONE 留空时任务默认时区跟随系统时区
        assert runtime["timezone"] == "UTC"
        assert runtime["scheduler_timezone"] == "UTC"

        broken = client.put(
            f"/api/v1/system/configs/{original['id']}", headers=headers, json={"value": "Mars/Olympus"}
        )
        assert broken.status_code == 422, broken.text
        assert broken.json()["code"] == "CONFIG_TIMEZONE_INVALID"
        # 校验失败不能留下半套状态：运行期与库里都得是上一个合法值
        assert current_timezone() == "UTC"
        assert _visible_timezone(client, headers) == ("UTC", "UTC")
        assert _timezone_config(client, headers)["value"] == "UTC"
    finally:
        restored = client.put(
            f"/api/v1/system/configs/{original['id']}", headers=headers, json={"value": "Asia/Shanghai"}
        )
        assert restored.status_code == 200, restored.text
    assert current_timezone() == "Asia/Shanghai"
    assert _visible_timezone(client, headers) == ("Asia/Shanghai", "Asia/Shanghai")
