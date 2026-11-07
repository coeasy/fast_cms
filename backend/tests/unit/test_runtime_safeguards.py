"""运行期保障：约束冲突语义、独立进程的事件订阅、调度器时区同步。

这三个问题都不在「单请求单文件」的视野里，只有把进程生命周期连起来看才会暴露：

1. **约束冲突**：各模块的「先查后插」预检挡不住并发，最终防线是数据库约束。
   没人接 `IntegrityError` 时，客户端把「数据重复」看成「服务器内部错误」。
2. **独立调度器**：`fastcms scheduler` 不经过 `create_app()`，但事件订阅是在
   各模块 `module.py` 里注册的。漏加载清单 → 任务失败通知被静默丢弃。
3. **跨进程时区**：事件总线是进程内的，Web 进程改时区，独立调度器收不到，
   只能靠重启。同步循环里比对一次库值即可自愈。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core import events
from app.core.errors import integrity_error_handler
from app.core.module import discover_modules

APP = Path(__file__).resolve().parents[2] / "app"


def _scheduler_source() -> ast.Module:
    return ast.parse((APP / "cli.py").read_text(encoding="utf-8"))


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"cli.py 里找不到函数 {name}")


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


async def test_integrity_error_becomes_409() -> None:
    exc = IntegrityError("INSERT INTO sys_role ...", {}, Exception("UNIQUE constraint failed"))
    response = await integrity_error_handler(None, exc)  # type: ignore[arg-type]
    assert response.status_code == 409
    body = response.body.decode("utf-8")
    assert "DATA_CONFLICT" in body
    # 异常原文含 SQL 与参数，绝不能出现在响应里
    assert "INSERT INTO" not in body


async def test_other_db_errors_still_500() -> None:
    """只有约束冲突走 409；连接断开之类的真实故障仍然是 500。"""
    response = await integrity_error_handler(None, OperationalError("x", {}, Exception()))  # type: ignore[arg-type]
    assert response.status_code == 500
    assert "INTERNAL_ERROR" in response.body.decode("utf-8")


def test_app_registers_integrity_handler() -> None:
    from app.main import create_app

    handlers = create_app().exception_handlers
    assert IntegrityError in handlers, "create_app() 必须注册 IntegrityError 兜底处理器"


def test_scheduler_bootstrap_loads_module_manifests() -> None:
    """独立调度器必须显式加载模块清单，否则事件订阅不存在。

    这是一条「源码级」断言：`fastcms scheduler` 的启动路径无法在单测里真跑
    （会阻塞在 while True），而漏掉这一步的后果是生产上通知静默丢失。
    """
    node = _function_node(_scheduler_source(), "_scheduler")
    assert "discover_modules" in _called_names(node), (
        "_scheduler() 必须调用 discover_modules()：事件订阅在模块清单里注册，"
        "不加载清单会让 job.finished 事件没有订阅方"
    )


def test_scheduler_loop_resyncs_timezone() -> None:
    """独立调度器要能感知「另一个进程改了时区」，不能要求重启。"""
    node = _function_node(_scheduler_source(), "_scheduler")
    names = _called_names(node)
    assert "load_timezone_from_db" in names
    assert "resync_timezone" in names, "同步循环里必须按库值时区重排任务"


def test_discovery_registers_job_finished_subscriber() -> None:
    """等价于「独立调度器启动后能收到任务事件」。"""
    discover_modules()
    assert events.subscribers("job.finished"), "job.finished 没有订阅方，任务失败通知会丢"


@pytest.mark.parametrize("event", sorted(events.KNOWN_EVENTS))
def test_known_events_have_receiver(event: str) -> None:
    discover_modules()
    assert events.subscribers(event), f"{event} 没有订阅方"
