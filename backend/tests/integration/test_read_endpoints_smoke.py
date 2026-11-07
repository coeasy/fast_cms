"""只读端点冒烟：所有无路径参数的 GET 都必须给出可解释的响应，不能 5xx。

为什么值得单独一道：契约检查是静态的（看得见路由和权限码），但看不见
「响应里塞了不可序列化的对象」这类运行期问题。真实踩过的一个例子是
``ipgeo.describe()`` 直接把内部 searcher（含 bytes buffer）放进响应，
FastAPI 编码时抛 ``UnicodeDecodeError``——查询一个正常 IP 就 500，
而静态检查、单测、契约 diff 全都发现不了。

判定口径：
- 200 / 404 / 422 都算合格（404 = 资源确实不存在，422 = 缺必填 query 参数）；
- 403 视为失败：本用例用管理员身份，只读端点不该拒绝管理员；
- 5xx 一律失败，并打印响应体方便定位。

带路径参数的 GET（``/system/users/{id}``、``/security/ip-locate/{ip}`` 等）不在这里，
它们需要真实 id / IP 才能构造请求，由 ``test_detail_endpoints.py`` 与
``test_ip_locate.py`` 覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import auth

SPEC = Path(__file__).resolve().parents[3] / "openapi.json"
ALLOWED = {200, 404, 422}


def _read_endpoints() -> list[str]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    return [
        path
        for path, operations in spec["paths"].items()
        if "get" in operations and "{" not in path
    ]


@pytest.mark.parametrize("path", _read_endpoints())
def test_read_endpoint_responds(client: TestClient, admin_token: str, path: str) -> None:
    resp = client.get(path, headers=auth(admin_token))
    assert resp.status_code < 500, f"{path} 返回 {resp.status_code}：{resp.text[:400]}"
    assert resp.status_code in ALLOWED, f"{path} 返回 {resp.status_code}：{resp.text[:400]}"


def test_spec_has_read_endpoints() -> None:
    """防止用例被「空参数化」悄悄跳过：至少要有几十个只读端点。"""
    assert len(_read_endpoints()) >= 30
