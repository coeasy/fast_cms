"""audit 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.ops.audit.router import router

MODULE = ModuleSpec(
    name="audit",
    order=50,
    description="登录日志与操作日志查询、导出",
    routers=(router,),
)
