"""系统模块服务门面。

把 ``users`` / ``roles`` / ``menus`` / ``depts`` / ``sessions`` 的公开函数集中
导出，供本模块的 router 与跨模块调用方（auth、monitor）使用。

**显式列出而不是 ``import *``**：星导入让「谁用了哪个函数」无法静态追溯，
也是历史上 ``revoke_user_sessions`` 双名导入踩坑的根源（星导入会跳过下划线
名字，别名导入又不会进 ``__all__``）。这里把两件事都写清楚。
"""

from __future__ import annotations

from app.modules.sys.system.services._common import (
    LOGIN_IP_PERMISSION,
    MAX_DEPT_ANCESTORS_LEN,
    MAX_DEPT_DEPTH,
    USERNAME_ADMIN,
    _can_access_user,
    _commit,
    _ensure_can_manage_superuser,
    _ensure_user_access,
    _log_operation,
    _normalize_username,
    _revoke_sessions,
    _user_payload,
    _user_scope,
    _validate_dept_path,
    ensure_user_access,
    log_operation,
    revoke_user_sessions,
)
from app.modules.sys.system.services.depts import (
    create_dept,
    delete_dept,
    list_depts,
    update_dept,
)
from app.modules.sys.system.services.menus import (
    create_menu,
    current_user_menu,
    delete_menu,
    list_menus,
    list_permissions,
    update_menu,
)
from app.modules.sys.system.services.roles import (
    assign_role_permissions,
    create_role,
    delete_role,
    get_role,
    list_role_options,
    list_roles,
    set_role_data_scope,
    update_role,
)
from app.modules.sys.system.services.sessions import (
    cleanup_old_sessions,
    kick_session,
    list_user_sessions,
    revoke_other_sessions,
    revoke_user_session,
    update_profile,
    write_login_log,
)
from app.modules.sys.system.services.users import (
    assign_user_roles,
    batch_users,
    create_user,
    delete_user,
    get_user,
    import_users,
    list_users,
    reset_user_password,
    set_user_status,
    update_user,
)

__all__ = [
    "LOGIN_IP_PERMISSION",
    "MAX_DEPT_ANCESTORS_LEN",
    "MAX_DEPT_DEPTH",
    "USERNAME_ADMIN",
    "_can_access_user",
    "_commit",
    "_ensure_can_manage_superuser",
    "_ensure_user_access",
    "_log_operation",
    "_normalize_username",
    "_revoke_sessions",
    "_user_payload",
    "_user_scope",
    "_validate_dept_path",
    "assign_role_permissions",
    "assign_user_roles",
    "batch_users",
    "cleanup_old_sessions",
    "create_dept",
    "create_menu",
    "create_role",
    "create_user",
    "current_user_menu",
    "delete_dept",
    "delete_menu",
    "delete_role",
    "delete_user",
    "ensure_user_access",
    "get_role",
    "get_user",
    "import_users",
    "kick_session",
    "list_depts",
    "list_menus",
    "list_permissions",
    "list_role_options",
    "list_roles",
    "list_user_sessions",
    "list_users",
    "log_operation",
    "reset_user_password",
    "revoke_other_sessions",
    "revoke_user_session",
    "revoke_user_sessions",
    "set_role_data_scope",
    "set_user_status",
    "update_dept",
    "update_menu",
    "update_profile",
    "update_role",
    "update_user",
    "write_login_log",
]
