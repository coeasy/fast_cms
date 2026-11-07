"""system 模块清单：声明路由，供 ``main.py`` 自动发现注册。"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.sys.system.router import router

MODULE = ModuleSpec(
    name="system",
    order=30,
    description="用户、角色、部门、菜单、权限、字典、系统参数",
    routers=(router,),
    # B-1：system 的 service 层（services/*.py）自行写操作日志，
    # 声明为 service 让中间件跳过 /system/**，避免双写。
    audit="service",
)
