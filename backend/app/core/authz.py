"""兼容 shim：鉴权上下文已上移到 ``app/kernel/authz.py``。

**为什么搬**：``authz`` 要查 ``User``/``Role``/``Permission``/``UserSession``，
还要读 ``ApiKey``——它是「有状态的底座」，不属于「无状态的 core」。此前它留在
``app/core``，导致 ``core → kernel`` 与 ``kernel → core`` 互相依赖（``kernel/db.py``、
``kernel/scope.py``、``kernel/files.py`` 都反向 import 了它），层次方向无法用门禁表达。

**为什么留 shim**：``authz`` 有 38 个文件、142 处 ``Depends`` 引用，一次性改导入路径
等于把重构风险和业务改动混在一起。这里只保留 re-export，调用点零改动；
下一轮直接删除本文件，把导入路径统一改成 ``app.kernel.authz``。
``scripts/check_modules.py`` 的白名单精确登记了本文件，新增反向依赖会立刻失败。
"""

from __future__ import annotations

from app.core.net import client_ip, client_ip_from_scope
from app.kernel.authz import (
    ANONYMOUS_USER,
    API_KEY_MARKER,
    PASSWORD_CHANGE_WHITELIST,
    AuthContext,
    DataScope,
    _AnonymousUser,
    _within_grace,
    apply_user_scope,
    bearer_scheme,
    get_current_user,
    get_data_scope,
    optional_auth,
    require_admin,
    require_permission,
    scope_user_ids,
    user_in_scope,
)

__all__ = [
    "ANONYMOUS_USER",
    "API_KEY_MARKER",
    "PASSWORD_CHANGE_WHITELIST",
    "AuthContext",
    "DataScope",
    "_AnonymousUser",
    "_within_grace",
    "apply_user_scope",
    "bearer_scheme",
    "client_ip",
    "client_ip_from_scope",
    "get_current_user",
    "get_data_scope",
    "optional_auth",
    "require_admin",
    "require_permission",
    "scope_user_ids",
    "user_in_scope",
]
