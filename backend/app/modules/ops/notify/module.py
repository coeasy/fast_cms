"""notify 模块清单：声明路由，并在加载时注册通知订阅。

订阅放在清单里而不是路由里：``discover_modules()`` 在应用启动时一定会导入
本文件，因此「公告发布」「任务结束」两个事件的接收方保证已就位；发布方
（content / jobs.runner）则完全不需要认识本模块。
"""

from __future__ import annotations

from app.core.module import ModuleSpec
from app.modules.ops.notify import hooks
from app.modules.ops.notify.router import router

MODULE = ModuleSpec(
    name="notify",
    order=120,
    description="通知渠道、事件与推送记录",
    routers=(router,),
)

# 事件订阅是模块级副作用：导入本清单即完成注册（重复导入幂等）
hooks.register()
