"""事件总线与模块解耦的守护测试。

`app/core/events.py` 引入的是一条「发布方只声明发生了什么、订阅方自己注册」
的通道。它替代了原先三处函数体内 import（content→notify、config→schedule），
本文件确保：

1. 总线本身行为正确（顺序、异常隔离、幂等、拒绝未登记事件）；
2. 真实模块加载后，每个已登记事件都有订阅方——否则发布出去等于静默丢弃；
3. 模块代码里不再出现函数体内跨模块 import（循环依赖的遮蔽手法）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core import events
from app.core.module import discover_modules

APP = Path(__file__).resolve().parents[2] / "app"
MODULES_DIR = APP / "modules"


@pytest.fixture(autouse=True)
def _restore_events(client):
    """每个用例前后恢复真实订阅表，避免测试互相污染。

    必须带上 ``client``：``publish()`` 会尽力而为地用全局 ``session_scope`` 写
    ``EventLog``。不带这个夹具时写入落到会话级模板库，污染后续所有「表里恰好
    N 行」的断言行数，结果随用例执行顺序漂移（曾让 test_event_outbox.py 偶发失败）。
    ``client`` 给每个用例一份独立模板库副本，写多少都不影响别人。
    """
    snapshot = {event: list(events.subscribers(event)) for event in events.KNOWN_EVENTS}
    yield
    events.reset()
    for event, handlers in snapshot.items():
        for handler in handlers:
            events._handlers[event].append(handler)


async def test_publish_calls_handlers_in_registration_order() -> None:
    calls: list[str] = []

    async def first(**payload):
        calls.append(f"first:{payload['value']}")

    async def second(**payload):
        calls.append(f"second:{payload['value']}")

    events.reset()
    events.subscribe("notice.published", first)
    events.subscribe("notice.published", second)
    handled = await events.publish("notice.published", value=1)
    assert calls == ["first:1", "second:1"]
    assert handled == 2


async def test_handler_failure_does_not_block_others() -> None:
    """单个订阅者抛异常不能影响其它订阅者，也不能让发布方 500。"""
    calls: list[str] = []

    async def boom(**payload):
        raise RuntimeError("推送渠道挂了")

    async def ok(**payload):
        calls.append("ok")

    events.reset()
    events.subscribe("notice.published", boom)
    events.subscribe("notice.published", ok)
    handled = await events.publish("notice.published")
    assert calls == ["ok"]
    assert handled == 1


async def test_subscribe_is_idempotent() -> None:
    calls: list[int] = []

    async def handler(**payload):
        calls.append(1)

    events.reset()
    events.subscribe("notice.published", handler)
    events.subscribe("notice.published", handler)
    await events.publish("notice.published")
    assert calls == [1]


def test_unknown_event_is_rejected() -> None:
    async def handler(**payload):
        return None

    with pytest.raises(ValueError):
        events.subscribe("something.unknown", handler)


def test_every_known_event_has_subscriber_after_discovery() -> None:
    """真实加载全部模块清单后，每个已登记事件都必须有人接。

    订阅发生在各模块的 module.py 里；`discover_modules()` 是应用启动必经之路，
    所以这条断言等价于「线上发布出去的事件不会没人接」。
    """
    discover_modules()
    missing = [event for event in sorted(events.KNOWN_EVENTS) if not events.subscribers(event)]
    assert not missing, f"这些事件没有订阅方，发布出去会被丢弃：{missing}"


def test_notice_published_event_reaches_notify() -> None:
    discover_modules()
    handlers = events.subscribers("notice.published")
    names = {getattr(handler, "__module__", "") for handler in handlers}
    assert any(name.startswith("app.modules.ops.notify") for name in names), names


def test_timezone_event_reaches_schedule() -> None:
    discover_modules()
    handlers = events.subscribers("config.timezone_changed")
    names = {getattr(handler, "__module__", "") for handler in handlers}
    assert any(name.startswith("app.modules.ops.schedule") for name in names), names


def _function_scoped_module_imports(path: Path) -> list[tuple[str, int]]:
    """与 scripts/check_modules.py 同口径：函数体里的 app.modules.* 绝对导入。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.ImportFrom)
                and inner.module
                and inner.module.startswith("app.modules.")
            ):
                hits.append((inner.module, inner.lineno))
    return hits


def test_no_function_scoped_cross_module_imports() -> None:
    """函数体内 import 其它模块 = 循环依赖的遮蔽手法，一律禁止。

    同模块内部的相对导入（`from .models import ...`）不算跨模块，不在此列。
    """
    offenders: list[str] = []
    for path in MODULES_DIR.rglob("*.py"):
        own = ".".join(path.relative_to(MODULES_DIR).parts[:-1])
        for target, lineno in _function_scoped_module_imports(path):
            rest = target[len("app.modules.") :]
            parts = rest.split(".")
            # 域分组（sys/ops）下取两级，否则取一级
            owner = ".".join(parts[:2]) if parts[0] in {"sys", "ops"} else parts[0]
            if owner != own and own.startswith(owner):
                continue  # 同模块内的子包
            if owner != own:
                offenders.append(f"{path.relative_to(APP).as_posix()}:{lineno} -> {target}")
    assert not offenders, "函数体内跨模块 import：" + "; ".join(offenders)
