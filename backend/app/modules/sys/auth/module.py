"""auth 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.sys.auth.router import router

MODULE = ModuleSpec(
    name="auth",
    order=10,
    description="登录、令牌刷新、登出、自助改密",
    routers=(router,),
)
