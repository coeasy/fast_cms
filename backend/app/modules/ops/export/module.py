"""export 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.ops.export.router import router

MODULE = ModuleSpec(
    name="export",
    order=80,
    description="批量导出任务与下载",
    routers=(router,),
)
