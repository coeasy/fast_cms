"""content 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.content.router import admin_router, router

MODULE = ModuleSpec(
    name="content",
    order=110,
    description="公告与文件管理",
    routers=(router, admin_router),
)
