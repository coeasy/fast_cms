"""平台级数据模型（内核层）——re-export 面。

按职责拆成四个子模块（``identity`` / ``credential`` / ``audit`` / ``storage``），
本包保持 ``app.kernel.models`` 这一导入面不变，存量 ``from app.kernel.models import X``
零迁移。新增平台模型时，在对应子模块里定义并在本包 re-export。
"""

from app.kernel.models.audit import LoginLog, OperationLog
from app.kernel.models.credential import (
    IP_RULE_KINDS,
    IP_RULE_SCOPES,
    IP_RULE_STATUSES,
    ApiKey,
    IpRule,
)
from app.kernel.models.events import EventLog, EventOutbox
from app.kernel.models.identity import (
    Dept,
    Menu,
    Permission,
    Role,
    User,
    UserSession,
    role_depts,
    role_permissions,
    user_roles,
)
from app.kernel.models.storage import FileObject

__all__ = [
    "IP_RULE_KINDS",
    "IP_RULE_SCOPES",
    "IP_RULE_STATUSES",
    "ApiKey",
    "Dept",
    "EventLog",
    "EventOutbox",
    "FileObject",
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
