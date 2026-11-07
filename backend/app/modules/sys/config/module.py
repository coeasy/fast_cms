"""config 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.sys.config.router import public_router, router

MODULE = ModuleSpec(
    name="config",
    order=40,
    description="运行期配置读取与公开配置下发",
    routers=(public_router, router),
)
