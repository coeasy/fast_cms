"""monitor 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.ops.monitor.router import router

MODULE = ModuleSpec(
    name="monitor",
    order=60,
    description="服务器状态、在线用户、IP 归属",
    routers=(router,),
)
