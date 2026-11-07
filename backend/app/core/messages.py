"""业务错误文案的单一来源（B-10 / 6-3）。

此前 ``AppError(code, "中文文案")`` 把文案散落在 200 多个调用点：同一个错误码
在不同位置可能写出不同措辞，改一次文案要全局搜索，想做多语言更是无从下手。
本表把「错误码 -> 默认文案」收敛到一处，调用点只写错误码：

    raise AppError("SYSTEM_USER_NOT_FOUND")

只留缝、不做翻译。三条规则：

1. ``MESSAGES`` 是**默认值**。需要带上下文的错误码（例如「不能授予超过自己的权限」
   与「不能授予全部数据范围」共用一个 ``SYSTEM_ROLE_ESCALATION``）仍可在调用点显式
   传 ``message``，这类错误码登记在 ``CONTEXT_MESSAGES``；其余错误码在调用点传字面量
   文案会被 ``scripts/check_contract.py`` 拦下——新增文案只能进本表。
2. 文案里可以写 ``{name}`` 占位符，调用方用 ``AppError(code, params={...})`` 填充。
   凡是模板化的条目，调用点必须给 ``params``，否则 ``message_for`` 只能返回带花括号
   的原文，门禁会报错。
3. 前端有语义一致的 ``t(key, fallback)``（``static/js/core/i18n.js``）：key 命中就用
   表里的，否则用 fallback。前端文案暂未集中，新代码用它即可。

新增错误码的完整流程：``core/errors.py`` 的 ``ERROR_CODE_STATUS`` 登记状态码 →
本表补默认文案 → 门禁校验两者一一对应，缺任何一半都会失败。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# 错误码 -> 默认文案。按错误码前缀分组，与 core/errors.py 的排序保持一致。
MESSAGES: dict[str, str] = {
    # --- AUDIT ---
    "AUDIT_BATCH_EMPTY": "请先选择要删除的日志",
    "AUDIT_BATCH_TOO_MANY": "批量删除的日志条数超过上限",
    "AUDIT_LOG_NOT_FOUND": "日志不存在",
    "AUDIT_RETENTION_TOO_SHORT": "至少保留 {days} 天日志",

    # --- AUTH ---
    "AUTH_ACCOUNT_BANNED": "账号已被封禁",
    "AUTH_ACCOUNT_LOCKED": "账号已锁定，请稍后再试",
    "AUTH_AVATAR_NOT_FOUND": "头像不存在",
    "AUTH_FORBIDDEN": "没有权限执行该操作",
    "AUTH_INVALID_CREDENTIALS": "用户名或密码错误",
    "AUTH_OLD_PASSWORD_INVALID": "当前密码不正确",
    "AUTH_ORIGIN_DENIED": "请求来源与当前站点不一致，已拒绝该跨源请求",
    "AUTH_PASSWORD_CHANGE_REQUIRED": "首次登录必须修改密码",
    "AUTH_PASSWORD_UNCHANGED": "新密码不能与当前密码相同",
    "AUTH_PERMISSION_SUNSET": "权限 {code} 已停止服务",
    "AUTH_RATE_LIMITED": "登录尝试过于频繁，请稍后再试",
    "AUTH_REFRESH_INVALID": "刷新凭证无效，请重新登录",
    "AUTH_REFRESH_REUSED": "检测到刷新凭证重复使用，会话已撤销",
    "AUTH_SERVICE_ACCOUNT_LOGIN": "服务账号不能直接登录，请使用 API Key",
    "AUTH_SESSION_EXPIRED": "会话已失效，请重新登录",
    "AUTH_SESSION_NOT_FOUND": "会话不存在或已失效",
    "AUTH_SESSION_REQUIRED": "当前身份没有登录会话",
    "AUTH_TOKEN_INVALID": "登录凭证无效",
    "AUTH_UNAUTHORIZED": "请先登录",
    "AUTH_USER_DISABLED": "账号不存在或已被禁用",
    "AUTH_WEAK_PASSWORD": "密码不符合安全策略",

    # --- CODEGEN ---
    "CODEGEN_COMPOSITE_KEY": "首版代码生成不支持联合主键",
    "CODEGEN_FILE_EXISTS": "文件已存在：{path}",
    "CODEGEN_MODULE_INVALID": "模块名必须为小写字母、数字或下划线",
    "CODEGEN_NO_PRIMARY_KEY": "表没有主键，无法生成 CRUD",
    "CODEGEN_PATH_INVALID": "生成文件路径非法",
    "CODEGEN_RESOURCE_INVALID": "资源名必须为小写字母、数字或下划线",
    "CODEGEN_TABLE_INVALID": "表名不合法",
    "CODEGEN_TABLE_NOT_FOUND": "数据表不存在",
    "CODEGEN_TABLE_SYSTEM": "系统表不允许生成业务代码",
    "CODEGEN_TARGET_EXISTS": "目标目录已存在且非空，不覆盖已有文件",

    # --- CONFIG ---
    "CONFIG_BUILTIN": "内置参数不可删除",
    "CONFIG_DICT_EXISTS": "字典编码已存在",
    "CONFIG_DICT_ITEM_EXISTS": "同字典下值已存在",
    "CONFIG_DICT_ITEM_NOT_FOUND": "字典项不存在",
    "CONFIG_DICT_NOT_FOUND": "字典类型不存在",
    "CONFIG_KEY_EXISTS": "参数键已存在",
    "CONFIG_NOT_FOUND": "系统参数不存在",
    "CONFIG_TIMEZONE_INVALID": "时区不合法",
    "CONFIG_VALUE_INVALID": "参数值不合法",

    # --- CONTENT ---
    "CONTENT_NOTICE_NOT_FOUND": "公告不存在",
    "CONTENT_NOTICE_NOT_PUBLISHED": "公告未处于已发布状态",

    # --- DATA ---
    "DATA_CONFLICT": "数据重复或违反约束，请检查后重试",

    # --- EXPORT ---
    "EXPORT_SPEC_UNKNOWN": "不支持的导出对象：{spec}",

    # --- IMPORT ---
    "IMPORT_COLUMN_MISSING": "缺少必需列",
    "IMPORT_COLUMN_UNKNOWN": "存在无法识别的列",
    "IMPORT_EMPTY": "文件没有内容",
    "IMPORT_ENCODING": "无法识别文件编码，请另存为 UTF-8 或 GBK 编码的 CSV",
    "IMPORT_ROW_INVALID": "导入的数据行不合法",
    "IMPORT_TOO_LARGE": "导入文件不能超过 {mb} MB",
    "IMPORT_TOO_MANY_ROWS": "单次最多导入 {max_rows} 行，请分批",

    # --- MONITOR ---
    "MONITOR_KICK_FORBIDDEN": "无权下线该用户",

    # --- NOTIFY ---
    "NOTIFY_CHANNEL_EXISTS": "同名通知渠道已存在",
    "NOTIFY_CHANNEL_NOT_FOUND": "通知渠道不存在",
    "NOTIFY_RECORD_CONFLICT": "测试消息排队失败，请稍后重试",
    "NOTIFY_RECORD_NOT_FOUND": "发送记录不存在",
    "NOTIFY_RECORD_SENDING": "该记录正在投递中，请稍后再试",
    "NOTIFY_RECORD_SENT": "该记录已发送成功，无需重试",
    "NOTIFY_URL_INVALID": "通知渠道地址不合法",

    # --- SCHEDULE ---
    "SCHEDULE_CRON_INVALID": "Cron 表达式不合法（需 5 段）",
    "SCHEDULE_CRON_REQUIRED": "Cron 任务必须填写表达式",
    "SCHEDULE_DATE_PAST": "执行时间必须晚于当前时间",
    "SCHEDULE_DATE_REQUIRED": "单次任务必须设置执行时间",
    "SCHEDULE_DATE_TIMEZONE_REQUIRED": "执行时间必须包含时区",
    "SCHEDULE_INTERVAL_INVALID": "间隔任务秒数必须不小于 {seconds}",
    "SCHEDULE_JOB_ARGS_INVALID": "任务参数不合法",
    "SCHEDULE_JOB_BUILTIN": "内置任务不可删除，只能暂停",
    "SCHEDULE_JOB_CODE_EXISTS": "Python 任务代码已存在",
    "SCHEDULE_JOB_CODE_FORBIDDEN": "缺少权限：schedule:job:code，无法管理 Python 脚本任务",
    "SCHEDULE_JOB_NOT_FOUND": "定时任务不存在",
    "SCHEDULE_JOB_TYPE_INVALID": "不支持的任务类型",
    "SCHEDULE_JOB_UNKNOWN": "任务代码未在注册表中定义",
    "SCHEDULE_LOG_NOT_FOUND": "任务日志不存在",
    "SCHEDULE_PYTHON_DISABLED": "Python 脚本任务已被全局禁用（SCHEDULER_PYTHON_ENABLED=false）",
    "SCHEDULE_SCRIPT_INVALID": "Python 脚本语法错误（第 {line} 行）：{msg}",
    "SCHEDULE_SCRIPT_REQUIRED": "Python 脚本不能为空",
    "SCHEDULE_SCRIPT_TOO_LARGE": "Python 脚本超过大小限制",
    "SCHEDULE_STATE_INVALID": "任务状态不合法",
    "SCHEDULE_TEMPLATE_BUILTIN": "内置任务模板受保护，不可修改或删除",
    "SCHEDULE_TEMPLATE_CODE_EXISTS": "任务模板编码已存在",
    "SCHEDULE_TEMPLATE_HANDLER_MISMATCH": "使用该模板时任务代码必须为模板处理器代码",
    "SCHEDULE_TEMPLATE_HANDLER_REQUIRED": "内置处理器模板必须选择处理器",
    "SCHEDULE_TEMPLATE_NOT_FOUND": "任务模板不存在",
    "SCHEDULE_TEMPLATE_TYPE_MISMATCH": "任务类型与模板不一致",
    "SCHEDULE_TIMEZONE_INVALID": "时区不合法",
    "SCHEDULE_TRIGGER_INVALID": "不支持的触发器类型",

    # --- SECURITY ---
    "SECURITY_API_KEY_DUPLICATED": "同名 API Key 已存在",
    "SECURITY_API_KEY_NOT_FOUND": "API Key 不存在",
    "SECURITY_API_KEY_SCOPE_FORBIDDEN": "不能授予自己不具备的权限：{codes}",
    "SECURITY_API_KEY_SCOPE_UNKNOWN": "权限码不存在：{codes}",
    "SECURITY_API_KEY_STATUS_INVALID": "状态只能是 active / disabled",
    "SECURITY_BAN_SUPERUSER": "仅超级管理员可封禁超级管理员",
    "SECURITY_IP_INVALID": "IP 地址或网段格式不合法",
    "SECURITY_IP_RULE_DUPLICATED": "同一网段与作用域的规则已存在",
    "SECURITY_IP_RULE_NOT_FOUND": "规则不存在",
    "SECURITY_IP_RULE_STATUS_INVALID": "状态只能是 active / disabled",
    "SECURITY_IP_UNKNOWN": "无法确定当前请求的来源 IP",
    "SECURITY_SELF_BAN": "不能封禁自己",

    # --- SYSTEM ---
    "SYSTEM_BATCH_ACTION_INVALID": "不支持的批量操作：{action}",
    "SYSTEM_BATCH_EMPTY": "请先选择要操作的用户",
    "SYSTEM_BATCH_TOO_MANY": "单次最多操作 {limit} 个用户",
    "SYSTEM_BUILTIN_USER": "内置管理员账号不可删除",
    "SYSTEM_DEPT_CYCLE": "部门上级不能是自己或自己的下级",
    "SYSTEM_DEPT_FORBIDDEN": "无权操作该部门",
    "SYSTEM_DEPT_HAS_CHILDREN": "存在子部门，无法删除",
    "SYSTEM_DEPT_IN_USE": "部门仍被引用，无法删除",
    "SYSTEM_DEPT_NOT_FOUND": "部门不存在",
    "SYSTEM_DEPT_TOO_DEEP": "部门层级过深",
    "SYSTEM_LAST_SUPERUSER": "系统必须保留至少一个超级管理员",
    "SYSTEM_MENU_BUILTIN": "内置菜单不可删除，可停用",
    "SYSTEM_MENU_CYCLE": "菜单上级不能形成循环",
    "SYSTEM_MENU_HAS_CHILDREN": "存在子节点，无法完成该操作",
    "SYSTEM_MENU_NOT_FOUND": "菜单不存在",
    "SYSTEM_MENU_PARENT_INVALID": "按钮不能作为上级菜单",
    "SYSTEM_MENU_PARENT_NOT_FOUND": "上级菜单不存在",
    "SYSTEM_MENU_PATH_EXISTS": "路由路径已存在",
    "SYSTEM_MENU_PATH_REQUIRED": "目录或菜单必须设置路由路径",
    "SYSTEM_MENU_PERMISSION_REQUIRED": "按钮必须绑定权限码",
    "SYSTEM_PERMISSION_NOT_FOUND": "权限码不存在",
    "SYSTEM_PROFILE_AVATAR_INVALID": "头像不合法",
    "SYSTEM_ROLE_BUILTIN": "内置角色受保护，不可修改或删除",
    "SYSTEM_ROLE_DISABLED": "角色「{role}」已停用",
    "SYSTEM_ROLE_ESCALATION": "不能授予超出自身权限范围的角色或数据范围",
    "SYSTEM_ROLE_EXISTS": "角色编码已存在",
    "SYSTEM_ROLE_IN_USE": "角色仍被用户引用，无法删除",
    "SYSTEM_ROLE_NOT_FOUND": "角色不存在",
    "SYSTEM_SELF_DELETE": "不能删除自己",
    "SYSTEM_SELF_DEMOTE": "不能取消自己的超级管理员身份",
    "SYSTEM_SELF_DISABLE": "不能禁用自己",
    "SYSTEM_USERNAME_EXISTS": "用户名已存在",
    "SYSTEM_USER_DEPT_FORBIDDEN": "目标部门不在你的数据范围内",
    "SYSTEM_USER_FORBIDDEN": "无权操作该用户",
    "SYSTEM_USER_NOT_FOUND": "用户不存在",
    "SYSTEM_WEAK_PASSWORD": "密码强度不足",

    # --- THEME ---
    "THEME_ACTIVE": "当前启用中的主题不可删除",
    "THEME_BUILTIN": "内置主题受保护，不可修改或删除",
    "THEME_CODE_EXISTS": "主题编码已存在",
    "THEME_NOT_FOUND": "主题模板不存在",

    # --- TOOL ---
    "TOOL_FILE_EMPTY": "不能上传空文件",
    "TOOL_FILE_FORBIDDEN": "无权访问该文件",
    "TOOL_FILE_MISSING": "文件内容已丢失",
    "TOOL_FILE_NOT_FOUND": "文件不存在",
    "TOOL_FILE_NO_EXTENSION": "文件缺少扩展名，无法判定类型",
    "TOOL_FILE_PATH_INVALID": "文件路径非法",
    "TOOL_FILE_TOO_LARGE": "文件超过大小限制",
    "TOOL_FILE_TYPE_FORBIDDEN": "头像只能是 PNG/JPEG/GIF/WebP/BMP 图片",
    "TOOL_FILE_TYPE_MISMATCH": "文件内容与图片格式不符",

}

# 同一错误码在不同场景需要不同措辞，允许调用点保留字面量。
#
# 这些码的默认文案是「最通用」的那个说法，具体场景（登录日志 vs 操作日志、
# 删除 vs 修改）由调用点给出。新增错误码**不要**往这里加——只有在确实无法用
# 一条文案覆盖多种场景时才登记，否则文案又散回去了。
CONTEXT_MESSAGES: frozenset[str] = frozenset(
    {
        "AUDIT_LOG_NOT_FOUND",
        "AUTH_ACCOUNT_BANNED",
        "AUTH_AVATAR_NOT_FOUND",
        "AUTH_FORBIDDEN",
        "AUTH_SESSION_EXPIRED",
        "AUTH_SESSION_NOT_FOUND",
        "AUTH_TOKEN_INVALID",
        "AUTH_USER_DISABLED",
        "CODEGEN_PATH_INVALID",
        "CONFIG_DICT_NOT_FOUND",
        "CONTENT_NOTICE_NOT_FOUND",
        "IMPORT_EMPTY",
        "MONITOR_KICK_FORBIDDEN",
        "NOTIFY_URL_INVALID",
        "SCHEDULE_TEMPLATE_BUILTIN",
        "SYSTEM_DEPT_CYCLE",
        "SYSTEM_DEPT_FORBIDDEN",
        "SYSTEM_DEPT_IN_USE",
        "SYSTEM_DEPT_NOT_FOUND",
        "SYSTEM_MENU_HAS_CHILDREN",
        "SYSTEM_PERMISSION_NOT_FOUND",
        "SYSTEM_PROFILE_AVATAR_INVALID",
        "SYSTEM_ROLE_BUILTIN",
        "SYSTEM_ROLE_ESCALATION",
        "SYSTEM_ROLE_NOT_FOUND",
        "SYSTEM_USER_FORBIDDEN",
        "THEME_BUILTIN",
    }
)


def message_for(code: str, params: Mapping[str, Any] | None = None) -> str:
    """取错误码的默认文案，并用 ``params`` 填充 ``{name}`` 占位符。

    - 未登记的错误码返回错误码本身：这里处在异常构造路径上，不能再抛异常，
      否则原始错误会被掩盖成 500；
    - 占位符与实参不匹配时退回模板原文，理由同上——宁可文案里带花括号，
      也不要在报错时再炸一次。
    """
    template = MESSAGES.get(code)
    if template is None:
        return code
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template
