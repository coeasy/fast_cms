"""security 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.sys.security.router import router

MODULE = ModuleSpec(
    name="security",
    order=100,
    description="IP 访问控制、用户封禁、API Key",
    routers=(router,),
    # B-1：security 的 service 层自行写操作日志（_audit/_commit_audit），
    # 声明为 service 让中间件跳过 /security/**，避免双写。
    audit="service",
)
