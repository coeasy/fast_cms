"""schedule 模块清单：声明路由，并订阅系统时区变更。

时区变化由 config 模块广播（``config.timezone_changed``），本模块负责重排
调度器；反过来 config 不需要 import 调度器，两个模块因此互不认识。
"""

from __future__ import annotations

from app.core import events
from app.core.module import ModuleSpec
from app.modules.ops.schedule.router import router
from app.modules.ops.schedule.scheduler import scheduler_service

MODULE = ModuleSpec(
    name="schedule",
    order=90,
    description="定时任务、模板与执行日志",
    routers=(router,),
)


async def _on_timezone_changed(*, zone: str) -> None:
    """时区变了：调度器实例的时区不可改，只能整体重建一次。"""
    del zone
    await scheduler_service.resync_timezone()


# 事件订阅是模块级副作用：导入本清单即完成注册（重复导入幂等）
events.subscribe("config.timezone_changed", _on_timezone_changed)
