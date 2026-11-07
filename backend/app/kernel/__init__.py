"""共享内核：被多个模块与 ``app/core`` 共同依赖的数据模型。

分层约定（引入内核后）：

    app/core      ← 基础设施（配置、错误、鉴权、中间件），不得 import app.modules
    app/kernel    ← 平台级数据模型（身份、访问凭证、审计记录），不得 import app.modules
    app/modules   ← 业务模块，只能依赖 core / kernel，模块之间互不 import

内核只放「谁都要用、且没有业务归属」的东西。用户、角色这类看起来像
「系统管理」的概念，实际是鉴权与数据范围的基础，放在 modules 里会让
``app/core`` 反向依赖业务模块，形成层次倒置。
"""

from app.kernel.models import (
    IP_RULE_KINDS,
    IP_RULE_SCOPES,
    IP_RULE_STATUSES,
    ApiKey,
    Dept,
    IpRule,
    LoginLog,
    Menu,
    OperationLog,
    Permission,
    Role,
    User,
    UserSession,
    role_depts,
    role_permissions,
    user_roles,
)

__all__ = [
    "IP_RULE_KINDS",
    "IP_RULE_SCOPES",
    "IP_RULE_STATUSES",
    "ApiKey",
    "Dept",
    "IpRule",
    "LoginLog",
    "Menu",
    "OperationLog",
    "Permission",
    "Role",
    "User",
    "UserSession",
    "role_depts",
    "role_permissions",
    "user_roles",
]
