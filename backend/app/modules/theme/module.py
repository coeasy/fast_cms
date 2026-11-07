"""theme 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.theme.router import public_router, router

MODULE = ModuleSpec(
    name="theme",
    order=20,
    description="主题模板管理与公开主题配置",
    routers=(public_router, router),
)
