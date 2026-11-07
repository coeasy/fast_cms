"""dashboard 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.ops.dashboard.router import router

MODULE = ModuleSpec(
    name="dashboard",
    order=70,
    description="工作台汇总指标",
    routers=(router,),
)
