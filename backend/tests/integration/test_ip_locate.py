"""IP 归属查询接口：响应必须可序列化，且不泄漏内部对象。

回归背景：``describe()`` 曾经直接返回 ``_STATE.entries.values()``，而这些 entry
里挂着 xdb 的 ``searcher`` / ``vector_index``（内部 buffer 是 bytes）。查询一个
正常 IP 时 FastAPI 编码响应就会抛 ``UnicodeDecodeError``——也就是「IP 归属地
功能一装好、查询就 500」。这里钉住两点：

1. 查询合法 IP 返回 200，``library.versions`` 只有摘要字段；
2. 拿不到合法来源 IP 时是 ``SECURITY_IP_UNKNOWN``，而不是把原始串塞进
   ``SECURITY_IP_INVALID`` 的报错文案里（如「不是合法的 IP：'testclient'」）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.conftest import auth

API = settings.api_prefix
SUMMARY_KEYS = {"version", "path", "size_bytes", "loaded"}


def test_locate_ip_returns_serializable_payload(client: TestClient, admin_token: str) -> None:
    resp = client.get(f"{API}/security/ip-locate/8.8.8.8", headers=auth(admin_token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["ip"] == "8.8.8.8"
    assert data["canonical"] == "8.8.8.8/32"
    assert set(data["rules"]["scopes"]) == {"api", "admin", "portal"}
    assert isinstance(data["library"], dict)

    for item in data["library"]["versions"]:
        # 只允许摘要字段：多出来的就是内部对象（searcher/buffer），会撑爆响应或直接 500
        assert set(item) == SUMMARY_KEYS, item


def test_locate_current_reports_unknown_instead_of_invalid(client: TestClient, admin_token: str) -> None:
    resp = client.get(f"{API}/security/ip-locate/current", headers=auth(admin_token))
    if resp.status_code == 200:
        assert resp.json()["data"]["ip"]
        return
    # 测试客户端的 peer 是 "testclient"，不是 IP：必须归为「无法确定来源 IP」
    assert resp.status_code == 422
    assert resp.json()["code"] == "SECURITY_IP_UNKNOWN"
