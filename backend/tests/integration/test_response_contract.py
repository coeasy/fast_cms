"""响应契约的**双向**校验（Phase 7）。

补响应模型只解决了「出站形状不对」，没解决「服务层多吐了字段」：Pydantic 默认
``extra="ignore"``，多出来的键会被静默丢弃——HTTP 200、无日志，前端只拿到 undefined。
本模块用两条断言把这个方向也钉住：

1. 所有 ``*Out`` 模型必须 ``extra="forbid"``（新增模型忘了继承 ``StrictModel`` 会失败）；
2. 实际响应里的字段必须都在模型声明内（按 OpenAPI 解析 ``Envelope[Page[T]]`` 的嵌套结构）。

再加一条**软检查**（只报告不失败）：模型声明了、但采样响应里从未出现的字段，
大概率是「加了模型字段却没在 ``*_out()`` 里赋值」的死字段。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.envelope import StrictModel
from tests.conftest import auth


def _resolve(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    """跟随 ``$ref`` 与 ``anyOf[T, null]``，拿到真正的对象 schema。"""
    seen = 0
    while isinstance(schema, dict) and seen < 10:
        if "$ref" in schema:
            schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
            seen += 1
            continue
        branches = schema.get("anyOf") or schema.get("oneOf")
        if branches:
            real = [item for item in branches if item.get("type") != "null"]
            if not real:
                return {}
            schema = real[0]
            seen += 1
            continue
        break
    return schema or {}


def _item_schema(spec: dict[str, Any], response: dict[str, Any]) -> dict[str, Any] | None:
    """从 200 响应里取出「单条数据」的 schema：兼容 ``Envelope[T]`` / ``Envelope[list[T]]`` / ``Envelope[Page[T]]``。"""
    content = (response.get("content") or {}).get("application/json")
    if not content:
        return None
    envelope = _resolve(spec, content.get("schema"))
    data = _resolve(spec, (envelope.get("properties") or {}).get("data"))
    if not data:
        return None
    if data.get("type") == "array":
        return _resolve(spec, data.get("items"))
    props = data.get("properties") or {}
    if "items" in props:
        items = _resolve(spec, props["items"])
        if items.get("type") == "array":
            return _resolve(spec, items.get("items"))
        nested = items.get("properties") or {}
        if "items" in nested:
            return _resolve(spec, nested["items"].get("items"))
    return data


def _out_models() -> list[type[StrictModel]]:
    from pydantic import BaseModel

    import app.kernel.schemas
    import app.modules.content.schemas
    import app.modules.ops.audit.schemas
    import app.modules.ops.monitor.schemas
    import app.modules.ops.notify.schemas
    import app.modules.ops.schedule.schemas
    import app.modules.sys.auth.schemas
    import app.modules.sys.config.schemas
    import app.modules.sys.security.schemas
    import app.modules.sys.system.schemas
    import app.modules.theme.schemas  # noqa: F401

    found: list[type[StrictModel]] = []
    stack: list[type[BaseModel]] = list(BaseModel.__subclasses__())
    seen: set[int] = set()
    while stack:
        model = stack.pop()
        if id(model) in seen:
            continue
        seen.add(id(model))
        stack.extend(model.__subclasses__())
        name = model.__name__
        if name.endswith(("Out", "Brief")) and model.__module__.startswith("app"):
            found.append(model)  # type: ignore[arg-type]
    return found


def test_every_out_model_forbids_extra_fields() -> None:
    """新增 ``*Out`` 模型必须继承 ``StrictModel``，否则多字段又会静默丢弃。"""
    offenders = [model.__name__ for model in _out_models() if model.model_config.get("extra") != "forbid"]
    assert not offenders, f"这些响应模型没有开启 extra='forbid'：{sorted(offenders)}"


def _read_operations() -> list[tuple[str, dict[str, Any]]]:
    from app.main import app

    spec = app.openapi()
    result: list[tuple[str, dict[str, Any]]] = []
    for path, operations in spec["paths"].items():
        if "get" not in operations or "{" in path:
            continue
        response = operations["get"].get("responses", {}).get("200", {})
        if _item_schema(spec, response) is None:
            continue
        result.append((path, spec))
    return result


@pytest.mark.parametrize("path,spec", _read_operations())
def test_response_fields_are_declared(client: TestClient, admin_token: str, path: str, spec: dict) -> None:
    """响应里出现的每个键都必须在响应模型里声明（多余键会被 forbid 拦成 500，这里给更清楚的报错）。"""
    response = spec["paths"][path]["get"]["responses"]["200"]
    target = _item_schema(spec, response) or {}
    allowed = set((target.get("properties") or {}).keys())
    if not allowed:
        pytest.skip("该响应没有可校验的对象结构")

    resp = client.get(path, headers=auth(admin_token))
    if resp.status_code != 200:
        pytest.skip(f"该端点当前返回 {resp.status_code}，由冒烟用例覆盖")

    data = (resp.json() or {}).get("data")
    samples: list[Any] = []
    if isinstance(data, list):
        samples = data[:3]
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        samples = data["items"][:3]
    elif isinstance(data, dict):
        samples = [data]

    for index, item in enumerate(samples):
        if not isinstance(item, dict):
            continue
        extra = set(item) - allowed
        assert not extra, f"{path} 第 {index} 条响应多出未声明字段 {sorted(extra)}（模型字段：{sorted(allowed)}）"


def test_no_always_absent_model_fields(client: TestClient, admin_token: str) -> None:
    """软检查：模型声明了、但所有采样响应都没出现过的字段（疑似死字段）。

    只报告不失败——可选字段在空数据下缺席是正常的，这个报告用于 review 时人工判断。
    """
    from app.main import app

    spec = app.openapi()
    seen: dict[str, set[str]] = {}
    declared: dict[str, set[str]] = {}
    for path, operations in spec["paths"].items():
        if "get" not in operations or "{" in path:
            continue
        target = _item_schema(spec, operations["get"].get("responses", {}).get("200", {}))
        if not target:
            continue
        fields = set((target.get("properties") or {}).keys())
        if not fields:
            continue
        declared[path] = fields
        resp = client.get(path, headers=auth(admin_token))
        if resp.status_code != 200:
            continue
        data = (resp.json() or {}).get("data")
        rows: list[Any] = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict) and isinstance(data.get("items"), list):
            rows = data["items"]
        elif isinstance(data, dict):
            rows = [data]
        present: set[str] = set()
        for row in rows:
            if isinstance(row, dict):
                present |= set(row)
        # 空列表说明库里没有数据，不能据此判定字段是死的
        if rows:
            seen[path] = present

    absent = {path: sorted(declared[path] - seen.get(path, set())) for path in seen}
    report = {path: fields for path, fields in absent.items() if fields}
    print("\n[响应字段覆盖] 从未出现的模型字段：")
    for path, fields in report.items():
        print(f"  {path}: {fields}")
    assert len(seen) >= 15, "采样端点太少，检查逻辑可能失效"
