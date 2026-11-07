"""兼容 shim：事件总线已上移到 ``app/kernel/events.py``。

**为什么搬**：总线本身不碰数据库，但 ``publish()`` 要给每个订阅者落一条 ``EventLog``、
``enqueue_event()`` 要写 ``EventOutbox``——这两个都是 ORM 模型，所以它属于有状态的底座。

**为什么留 shim**：调用方一律写成 ``from app.core import events`` 再 ``events.publish(...)``，
共 8 处（含 3 个测试）。re-export 让这些调用点零改动，同时 ``events._handlers`` 与
``events.reset()`` 操作的是同一个 dict / 同一个函数对象，测试里的原地修改照旧生效。
下一轮删除本文件即可。
"""

from __future__ import annotations

from app.kernel.events import (
    KNOWN_EVENTS,
    Handler,
    _handlers,
    drain_event_outbox,
    enqueue_event,
    publish,
    reset,
    subscribe,
    subscribers,
)

__all__ = [
    "KNOWN_EVENTS",
    "Handler",
    "_handlers",
    "drain_event_outbox",
    "enqueue_event",
    "publish",
    "reset",
    "subscribe",
    "subscribers",
]
