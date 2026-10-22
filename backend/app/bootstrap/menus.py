from __future__ import annotations

from typing import Any

from app.bootstrap.permissions import ADMIN_PERMISSION

# component_key values map to lazily imported page modules in the frontend.
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
