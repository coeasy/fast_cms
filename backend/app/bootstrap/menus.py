from __future__ import annotations

from typing import Any

from app.bootstrap.permissions import ADMIN_PERMISSION

# 菜单树的内置种子。约定：
# - ``path`` 才是前端真正跳转的目标，必须与 app.web.router 的页面路径一一对应
#   （scripts/check_contract.py 会成对校验，菜单点进去 404 或页面没入口都会失败）；
# - ``component_key`` 是 sys_menu 的持久化字段，随接口下发给外部集成方使用。
#   当前这套前端**不**依赖它：页面脚本由 body[data-page] 决定（见 static/js/app.js），
#   所以改 component_key 不会影响本仓库的页面加载，不要误以为它是路由键。
MENU_TREE: list[dict[str, Any]] = [
    {
        "name": "工作台",
        "path": "/dashboard",
        "component_key": "dashboard",
        "icon": "DashboardOutlined",
        "permission_code": "dashboard:view",
        "sort": 1,
    },
    {
        "name": "系统管理",
        "path": "/system",
        "type": "directory",
        "icon": "SettingOutlined",
        "permission_code": ADMIN_PERMISSION,
        "sort": 10,
        "children": [
            {"name": "用户管理", "path": "/system/user", "component_key": "system/user", "permission_code": "system:user:list", "sort": 1},
            {"name": "角色管理", "path": "/system/role", "component_key": "system/role", "permission_code": "system:role:list", "sort": 2},
            {"name": "菜单管理", "path": "/system/menu", "component_key": "system/menu", "permission_code": "system:menu:list", "sort": 3},
            {"name": "部门管理", "path": "/system/dept", "component_key": "system/dept", "permission_code": "system:dept:list", "sort": 4},
            {"name": "权限清单", "path": "/system/permission", "component_key": "system/permission", "permission_code": "system:permission:list", "sort": 5},
            {"name": "字典管理", "path": "/system/dict", "component_key": "system/dict", "permission_code": "system:dict:list", "sort": 6},
            {"name": "系统参数", "path": "/system/config", "component_key": "system/config", "permission_code": "system:config:list", "sort": 7},
            {"name": "主题模板", "path": "/system/theme", "component_key": "system/theme", "permission_code": "system:theme:list", "sort": 8},
        ],
    },
    {
        "name": "安全管理",
        "path": "/security",
        "type": "directory",
        "icon": "SafetyOutlined",
        "permission_code": ADMIN_PERMISSION,
        "sort": 15,
        "children": [
            {"name": "IP 访问控制", "path": "/security/ip-rule", "component_key": "security/ip-rule", "permission_code": "security:ip-rule:list", "sort": 1},
            {"name": "用户封禁", "path": "/security/ban", "component_key": "security/ban", "permission_code": "security:user-ban:list", "sort": 2},
            {"name": "API Key", "path": "/security/api-key", "component_key": "security/api-key", "permission_code": "security:api-key:list", "sort": 3},
            {"name": "IP 归属查询", "path": "/security/ip-locate", "component_key": "security/ip-locate", "permission_code": "security:ip-locate:list", "sort": 4},
        ],
    },
    {
        "name": "系统监控",
        "path": "/monitor",
        "type": "directory",
        "icon": "MonitorOutlined",
        "permission_code": ADMIN_PERMISSION,
        "sort": 20,
        "children": [
            {"name": "在线用户", "path": "/monitor/online", "component_key": "monitor/online", "permission_code": "monitor:online:list", "sort": 1},
            {"name": "服务器监控", "path": "/monitor/server", "component_key": "monitor/server", "permission_code": "monitor:server:read", "sort": 2},
            {"name": "登录日志", "path": "/monitor/login-log", "component_key": "monitor/login-log", "permission_code": "monitor:login-log:list", "sort": 3},
            {"name": "操作日志", "path": "/monitor/operation-log", "component_key": "monitor/operation-log", "permission_code": "monitor:operation-log:list", "sort": 4},
            {"name": "事件投递日志", "path": "/monitor/event-log", "component_key": "monitor/event-log", "permission_code": "monitor:event-log:list", "sort": 5},
        ],
    },
    {
        "name": "定时任务",
        "path": "/schedule",
        "type": "directory",
        "icon": "ScheduleOutlined",
        "permission_code": ADMIN_PERMISSION,
        "sort": 30,
        "children": [
            {"name": "任务管理", "path": "/schedule/job", "component_key": "schedule/job", "permission_code": "schedule:job:list", "sort": 1},
            {"name": "任务模板", "path": "/schedule/template", "component_key": "schedule/template", "permission_code": "schedule:template:list", "sort": 2},
            {"name": "执行日志", "path": "/schedule/log", "component_key": "schedule/log", "permission_code": "schedule:log:list", "sort": 3},
        ],
    },
    {
        "name": "内容管理",
        "path": "/content",
        "type": "directory",
        "icon": "SoundOutlined",
        "permission_code": ADMIN_PERMISSION,
        "sort": 40,
        "children": [
            {"name": "公告管理", "path": "/content/notice", "component_key": "content/notice", "permission_code": "system:notice:list", "sort": 1},
        ],
    },
    {
        "name": "消息通知",
        "path": "/notify",
        "type": "directory",
        "icon": "BellOutlined",
        "permission_code": ADMIN_PERMISSION,
        "sort": 45,
        "children": [
            {"name": "通知渠道", "path": "/notify/channel", "component_key": "notify/channel", "permission_code": "notify:channel:list", "sort": 1},
            {"name": "发送记录", "path": "/notify/record", "component_key": "notify/record", "permission_code": "notify:record:list", "sort": 2},
        ],
    },
    {
        "name": "工具中心",
        "path": "/tool",
        "type": "directory",
        "icon": "ToolOutlined",
        "permission_code": ADMIN_PERMISSION,
        "sort": 50,
        "children": [
            {"name": "文件管理", "path": "/tool/file", "component_key": "tool/file", "permission_code": "tool:file:list", "sort": 1},
            {"name": "代码生成", "path": "/tool/codegen", "component_key": "tool/codegen", "permission_code": "tool:codegen:list", "sort": 2},
        ],
    },
    {
        "name": "个人中心",
        "path": "/account/profile",
        "component_key": "account/profile",
        "icon": "UserOutlined",
        "visible": False,
        "sort": 90,
    },
]


def iter_menus(nodes: list[dict[str, Any]], parent_path: str | None = None):
    for node in nodes:
        data = dict(node)
        children = data.pop("children", [])
        yield data, parent_path
        yield from iter_menus(children, data["path"])


def all_menu_tree() -> list[dict[str, Any]]:
    """存量 ``MENU_TREE`` + 各模块 ``module.py`` 声明的增量菜单。

    与权限码同样的取舍：内置菜单树是「信息架构」，一个目录横跨多个模块，
    留在本文件；新模块在自己的清单里声明一条顶层入口即可，不必改这里。

    同路径以存量优先，防止模块声明意外覆盖内置菜单。延迟导入
    ``app.core.module``：本模块在 ``bootstrap.seed`` 早期就被导入。
    """
    from app.core.module import module_menus

    extra = module_menus()
    if not extra:
        return list(MENU_TREE)
    known = {data["path"] for data, _parent in iter_menus(MENU_TREE)}
    merged = list(MENU_TREE)
    for item in extra:
        path = item.get("path")
        if not path or path in known:
            continue
        known.add(path)
        merged.append(dict(item))
    return merged
