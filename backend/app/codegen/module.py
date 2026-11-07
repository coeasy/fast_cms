"""codegen 模块清单：代码生成器作为平台能力纳入模块体系（A-2 / Phase 2-1）。

此前 ``app.codegen`` 是唯一游离在模块清单外的路由，由 ``main.py`` 硬编码
``include_router(codegen_router)``。现在改走清单：``discover_modules()`` 同时扫描
``app/modules`` 与 ``app/codegen``，注册顺序由 ``order`` 决定（200，最后注册），
与业务模块共用同一套发现/校验/注册链路。
"""

from __future__ import annotations

from app.codegen.router import router
from app.core.module import ModuleSpec

MODULE = ModuleSpec(
    name="codegen",
    order=200,
    description="代码生成器（单表 CRUD，经 safe_title 中和标题）",
    routers=(router,),
)
