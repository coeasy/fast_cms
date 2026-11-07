from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.menus import all_menu_tree, iter_menus
from app.bootstrap.models import BootstrapVersion
from app.bootstrap.permissions import (
    ADMIN_PERMISSION,
    READ_ONLY_PERMISSIONS,
    all_permission_codes,
    all_permissions,
)
from app.core.config import settings
from app.core.security import hash_password_async
from app.core.tz import scheduler_timezone
from app.db.base import utcnow
from app.kernel.models import Dept, Menu, Permission, Role, User
from app.modules.ops.schedule.models import Job, JobTemplate
from app.modules.sys.config.models import DictItem, DictType, SystemConfig
from app.modules.theme.models import ThemeTemplate
from app.modules.theme.presets import BUILTIN_THEMES, DEFAULT_THEME_CODE

logger = logging.getLogger("fastcms.bootstrap")

DEFAULT_DEPTS = [
    {"name": "总部", "leader": "系统管理员", "sort": 1},
]

DICT_TYPES: list[dict[str, Any]] = [
    {
        "name": "用户状态",
        "code": "user_status",
        "items": [
            {"label": "正常", "value": "active", "tag_color": "green", "sort": 1},
            {"label": "停用", "value": "disabled", "tag_color": "red", "sort": 2},
        ],
    },
    {
        "name": "通用状态",
        "code": "common_status",
        "items": [
            {"label": "启用", "value": "active", "tag_color": "green", "sort": 1},
            {"label": "停用", "value": "disabled", "tag_color": "red", "sort": 2},
        ],
    },
    {
        "name": "是否",
        "code": "yes_no",
        "items": [
            {"label": "是", "value": "true", "tag_color": "green", "sort": 1},
            {"label": "否", "value": "false", "tag_color": "default", "sort": 2},
        ],
    },
    {
        "name": "任务状态",
        "code": "job_status",
        "items": [
            {"label": "启用", "value": "enabled", "tag_color": "green", "sort": 1},
            {"label": "暂停", "value": "paused", "tag_color": "orange", "sort": 2},
        ],
    },
    {
        "name": "触发器类型",
        "code": "job_trigger",
        "items": [
            {"label": "Cron 表达式", "value": "cron", "tag_color": "blue", "sort": 1},
            {"label": "固定间隔", "value": "interval", "tag_color": "cyan", "sort": 2},
            {"label": "单次执行", "value": "date", "tag_color": "purple", "sort": 3},
        ],
    },
    {
        "name": "任务类型",
        "code": "job_type",
        "items": [
            {"label": "内置处理器", "value": "registry", "tag_color": "blue", "sort": 1},
            {"label": "Python 脚本", "value": "python", "tag_color": "purple", "sort": 2},
        ],
    },
    {
        "name": "任务日志状态",
        "code": "job_log_status",
        "items": [
            {"label": "执行中", "value": "running", "tag_color": "blue", "sort": 1},
            {"label": "成功", "value": "success", "tag_color": "green", "sort": 2},
            {"label": "失败", "value": "failed", "tag_color": "red", "sort": 3},
            {"label": "跳过", "value": "skipped", "tag_color": "orange", "sort": 4},
            {"label": "已中断", "value": "interrupted", "tag_color": "red", "sort": 5},
        ],
    },
    {
        "name": "公告状态",
        "code": "notice_status",
        "items": [
            {"label": "草稿", "value": "draft", "tag_color": "default", "sort": 1},
            {"label": "已发布", "value": "published", "tag_color": "green", "sort": 2},
            {"label": "已下线", "value": "offline", "tag_color": "default", "sort": 3},
        ],
    },
    {
        "name": "通知渠道类型",
        "code": "notify_channel_type",
        "items": [
            {"label": "钉钉机器人", "value": "dingtalk", "tag_color": "blue", "sort": 1},
            {"label": "飞书机器人", "value": "feishu", "tag_color": "cyan", "sort": 2},
            {"label": "企业微信机器人", "value": "wecom", "tag_color": "green", "sort": 3},
            {"label": "通用 Webhook", "value": "webhook", "tag_color": "geekblue", "sort": 4},
        ],
    },
    {
        "name": "通知事件",
        "code": "notify_event",
        "items": [
            {"label": "任务执行失败", "value": "job.failed", "tag_color": "red", "sort": 1},
            {"label": "任务被中断", "value": "job.interrupted", "tag_color": "orange", "sort": 2},
            {"label": "公告发布", "value": "notice.published", "tag_color": "green", "sort": 3},
            {"label": "渠道测试", "value": "test", "tag_color": "default", "sort": 4},
        ],
    },
    {
        "name": "通知发送状态",
        "code": "notify_send_status",
        "items": [
            {"label": "待发送", "value": "pending", "tag_color": "default", "sort": 1},
            {"label": "发送中", "value": "sending", "tag_color": "blue", "sort": 2},
            {"label": "已送达", "value": "success", "tag_color": "green", "sort": 3},
            {"label": "发送失败", "value": "failed", "tag_color": "red", "sort": 4},
            {"label": "已跳过", "value": "skipped", "tag_color": "orange", "sort": 5},
        ],
    },
    {
        "name": "数据范围",
        "code": "data_scope",
        "items": [
            {"label": "全部数据", "value": "all", "tag_color": "blue", "sort": 1},
            {"label": "本部门", "value": "dept", "tag_color": "cyan", "sort": 2},
            {"label": "本部门及以下", "value": "dept_and_below", "tag_color": "geekblue", "sort": 3},
            {"label": "仅本人", "value": "self", "tag_color": "purple", "sort": 4},
            {"label": "自定义部门", "value": "custom", "tag_color": "magenta", "sort": 5},
        ],
    },
    {
        "name": "登录结果",
        "code": "login_status",
        "items": [
            {"label": "成功", "value": "success", "tag_color": "green", "sort": 1},
            {"label": "失败", "value": "failure", "tag_color": "red", "sort": 2},
        ],
    },
]

SYSTEM_CONFIGS: list[dict[str, Any]] = [
    {"key": "system.title", "name": "系统名称", "value": "Fast CMS 管理后台", "group": "basic", "is_public": True},
    {"key": "system.short_title", "name": "系统简称", "value": "Fast CMS", "group": "basic", "is_public": True},
    # 前台首页 hero 标题读这个键；此前 portal.js 读 system.description 而库里
    # 根本没有，配置项改了不生效、hero 永远退回模板硬编码文案。
    {
        "key": "system.description",
        "name": "站点简介",
        "value": "轻量、可靠的一体化后台管理系统",
        "group": "basic",
        "is_public": True,
    },
    {"key": "system.copyright", "name": "版权信息", "value": "© 2026 Fast CMS", "group": "basic", "is_public": True},
    {
        "key": "system.login_description",
        "name": "登录页描述",
        "value": "轻量、完整、可扩展的 FastAPI 后台管理脚手架",
        "group": "basic",
        "is_public": True,
    },
    {"key": "system.icp", "name": "ICP 备案号", "value": "", "group": "basic", "is_public": True},
    {
        "key": "system.timezone",
        "name": "系统时区",
        "value": settings.app_timezone,
        "group": "basic",
        "is_public": True,
        "remark": "IANA 时区名，决定后台所有时间字段的展示时区与定时任务的默认时区；数据库仍以 UTC 存储",
    },
    {
        "key": "system.theme",
        "name": "当前主题",
        "value": "classic-blue",
        "group": "appearance",
        "is_public": True,
    },
    {
        "key": "security.password_min_length",
        "name": "密码最小长度",
        "value": "8",
        "type": "int",
        "group": "security",
        "is_public": False,
    },
]

BUILTIN_JOB_TEMPLATES: list[dict[str, Any]] = [
    {
        "code": "preset.cleanup_sessions",
        "name": "清理过期会话",
        "description": "内置清理任务模板，可调整保留天数后创建",
        "job_type": "registry",
        "handler_code": "system.cleanup_sessions",
        "args_json": {"days": settings.retention_days},
        "trigger_type": "cron",
        "cron_expr": "0 3 * * *",
        "timeout_seconds": 300,
    },
    {
        "code": "preset.cleanup_logs",
        "name": "清理过期日志",
        "description": "内置日志清理任务模板，可调整保留天数后创建",
        "job_type": "registry",
        "handler_code": "system.cleanup_logs",
        "args_json": {"days": settings.retention_days},
        "trigger_type": "cron",
        "cron_expr": "10 3 * * *",
        "timeout_seconds": 600,
    },
    {
        "code": "preset.heartbeat",
        "name": "心跳检测",
        "description": "周期性输出心跳信息，用于验证调度器运行状态",
        "job_type": "registry",
        "handler_code": "system.heartbeat",
        "args_json": {"message": "heartbeat"},
        "trigger_type": "interval",
        "interval_seconds": 60,
        "timeout_seconds": 60,
    },
    {
        "code": "preset.python_healthcheck",
        "name": "Python 健康检查示例",
        "description": "演示 Python 脚本任务：读取任务参数并输出环境信息，需要 Python 脚本权限",
        "job_type": "python",
        "script_code": (
            "import json\n"
            "import os\n"
            "import sys\n"
            "\n"
            "args = json.loads(os.environ.get(\"FASTCMS_JOB_ARGS\", \"{}\"))\n"
            "print(\"python job ok\", args)\n"
            "print(\"python\", sys.version.split()[0])\n"
        ),
        "args_json": {"message": "hello from python"},
        "trigger_type": "interval",
        "interval_seconds": 300,
        "timeout_seconds": 60,
    },
    {
        "code": "preset.notify_dispatch",
        "name": "通知消息投递",
        "description": "周期性把待发送的通知记录推送到 IM 渠道，需要先配置渠道再启用",
        "job_type": "registry",
        "handler_code": "notify.dispatch",
        "args_json": {"limit": 20},
        "trigger_type": "interval",
        "interval_seconds": 60,
        "timeout_seconds": 120,
    },
    {
        "code": "preset.event_outbox_drain",
        "name": "事件发件箱派发",
        "description": "周期性派发事务性事件发件箱里的待投递事件",
        "job_type": "registry",
        "handler_code": "event.outbox_drain",
        "args_json": {"limit": 100},
        "trigger_type": "interval",
        "interval_seconds": 30,
        "timeout_seconds": 120,
    },
]

BUILTIN_JOBS: list[dict[str, Any]] = [
    {
        "code": "system.cleanup_sessions",
        "name": "清理过期会话",
        "description": "每天凌晨清理超过保留天数且已过期的登录会话",
        "trigger_type": "cron",
        "cron_expr": "0 3 * * *",
        "args_json": {"days": settings.retention_days},
        "status": "enabled",
        "timeout_seconds": 300,
    },
    {
        "code": "system.cleanup_logs",
        "name": "清理过期日志",
        "description": "每天凌晨清理登录、操作、任务与通知发送记录",
        "trigger_type": "cron",
        "cron_expr": "10 3 * * *",
        "args_json": {"days": settings.retention_days},
        "status": "enabled",
        "timeout_seconds": 600,
    },
    {
        "code": "system.cleanup_files",
        "name": "清理未引用文件",
        "description": "每周清理未被引用的上传文件，默认暂停以免误删",
        "trigger_type": "cron",
        "cron_expr": "0 4 * * 0",
        "args_json": {"days": settings.retention_days},
        "status": "paused",
        "timeout_seconds": 600,
    },
    {
        "code": "system.heartbeat",
        "name": "示例心跳任务",
        "description": "验证调度器运行状态，默认暂停",
        "trigger_type": "interval",
        "interval_seconds": 60,
        "args_json": {"message": "heartbeat"},
        "status": "paused",
        "timeout_seconds": 60,
    },
    {
        "code": "notify.dispatch",
        "name": "通知消息投递",
        # 默认启用：投递失败会进入 pending 并带退避时间等待重试，而这个任务
        # 是唯一消费 pending 的入口。默认暂停等于「首次投递失败就永远卡在
        # pending」，用户只能到通知记录页手动点重试——一条隐性运维要求。
        "description": "每分钟投递待发送的通知并重试失败记录；不需要时可暂停",
        "trigger_type": "interval",
        "interval_seconds": 60,
        "args_json": {"limit": 20},
        "status": "enabled",
        "timeout_seconds": 120,
    },
    {
        "code": "event.outbox_drain",
        "name": "事件发件箱派发",
        # 默认启用：outbox 是唯一能让「事后通知」跨进程一致投递的入口。默认暂停
        # 会让事件永远积压在发件箱，管理端能看到却无人消费——一条隐性运维要求。
        "description": "每 30 秒派发事务性事件发件箱；多实例由分布式锁去重",
        "trigger_type": "interval",
        "interval_seconds": 30,
        "args_json": {"limit": 100},
        "status": "enabled",
        "timeout_seconds": 120,
    },
]


# 种子版本是「内置数据是否需要重新播种」的开关。
# 旧做法是手工递增的版本号（如 "1.8.0"），前提是「改了内置声明必须记得改版本」——
# 忘了递增，新增的内置菜单就永远不出现：入口存在但点不进去，是最难查的一种断链。
# 现在改为**内容指纹**（B-11 / Phase 12-2）：对全部内置声明的稳定 JSON 序列化做
# sha256。改了任何一条声明，指纹自动变化、下次启动自动重播；没改就绝不重播。
# 注意：管理员口令等运行时输入**不在**指纹里（它们不是种子声明）。
def _seed_fingerprint() -> str:
    import hashlib
    import json

    material = json.dumps(
        {
            "menus": all_menu_tree(),
            "permissions": all_permissions(),
            "dict_types": DICT_TYPES,
            "system_configs": SYSTEM_CONFIGS,
            "default_depts": DEFAULT_DEPTS,
            "themes": BUILTIN_THEMES,
            "job_templates": BUILTIN_JOB_TEMPLATES,
            "jobs": BUILTIN_JOBS,
            # 内置角色的授权也是种子声明：审计员只读集合变化时必须重播，
            # 否则旧库的 auditor 权限停在上一版声明（B-11 的盲区）。
            "role_grants": {
                "super_admin": sorted(all_permission_codes()),
                "auditor": sorted(READ_ONLY_PERMISSIONS | {ADMIN_PERMISSION}),
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return "fp-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


SEED_VERSION = _seed_fingerprint()


async def _sync_permissions(db: AsyncSession) -> dict[str, Permission]:
    existing = {item.code: item for item in (await db.execute(select(Permission))).scalars()}
    now = utcnow()
    # 基线表 + 各模块 module.py 声明的增量权限码
    for item in all_permissions():
        current = existing.get(item["code"])
        if current is None:
            current = Permission(code=item["code"], name=item["name"], module=item["module"], type="api")
            db.add(current)
            existing[current.code] = current
        else:
            current.name = item["name"]
            current.module = item["module"]
            current.deprecated_at = None
    for code, perm in existing.items():
        if code not in all_permission_codes() and perm.deprecated_at is None:
            perm.deprecated_at = now
    await db.flush()
    return existing


async def _sync_menus(db: AsyncSession) -> None:
    rows = (await db.execute(select(Menu))).scalars().all()
    existing = {item.path: item for item in rows if item.path}
    flat = list(iter_menus(all_menu_tree()))
    for data, parent_path in flat:
        menu = existing.get(data["path"])
        if menu is None:
            menu = Menu(
                path=data["path"],
                visible=data.get("visible", True),
                status="active",
            )
            db.add(menu)
            existing[data["path"]] = menu
        menu.name = data["name"]
        menu.type = data.get("type", "menu")
        menu.component_key = data.get("component_key")
        menu.icon = data.get("icon")
        menu.sort = data.get("sort", 0)
        # visible/status 只在首次创建时取种子值：管理员停用或隐藏的内置菜单不该在下次 seed 时被恢复
        menu.permission_code = data.get("permission_code")
        menu.is_builtin = True
        menu.parent_id = existing[parent_path].id if parent_path and existing.get(parent_path) else None
        await db.flush()
    # 菜单改版（改名或下架）后，旧的内置行仍会被列表与路由接口返回，前端点进去直接 404，随种子一起清理。
    # sys_menu 的 parent_id 是 ON DELETE CASCADE，直接删会连带干掉管理员挂在下面的自定义菜单，
    # 所以只在没有子节点时删除，有子节点就停用保留结构，交给管理员自行迁移。
    tree_paths = {data["path"] for data, _parent in flat}
    parents_with_children = {item.parent_id for item in rows if item.parent_id is not None}
    for item in rows:
        if not item.is_builtin or not item.path or item.path in tree_paths:
            continue
        if item.id in parents_with_children:
            item.status = "disabled"
        else:
            await db.delete(item)
    for data, _parent in flat:
        menu = existing[data["path"]]
        children = [item for item in flat if item[1] == data["path"]]
        if children:
            menu.redirect = children[0][0]["path"]


async def _seed_dept(db: AsyncSession) -> Dept:
    dept = (await db.execute(select(Dept).where(Dept.name == "总部", Dept.parent_id.is_(None)))).scalars().first()
    if dept is None:
        dept = Dept(name="总部", ancestors="", leader="系统管理员", sort=1, status="active")
        db.add(dept)
        await db.flush()
    return dept


async def _seed_roles(db: AsyncSession, permissions: dict[str, Permission]) -> dict[str, Role]:
    roles = {item.code: item for item in (await db.execute(select(Role))).scalars()}
    super_role = roles.get("super_admin")
    if super_role is None:
        super_role = Role(code="super_admin", name="超级管理员", data_scope="all", sort=1)
        db.add(super_role)
        roles["super_admin"] = super_role
    super_role.name = "超级管理员"
    super_role.status = "active"
    super_role.is_builtin = True
    super_role.data_scope = "all"
    super_role.permissions = [permissions[code] for code in sorted(all_permission_codes())]

    auditor = roles.get("auditor")
    if auditor is None:
        auditor = Role(code="auditor", name="审计员", data_scope="all", sort=2)
        db.add(auditor)
        roles["auditor"] = auditor
    auditor.name = "审计员"
    auditor.status = "active"
    auditor.is_builtin = True
    auditor.data_scope = "all"
    auditor.permissions = [
        permissions[code]
        for code in sorted(READ_ONLY_PERMISSIONS | {ADMIN_PERMISSION})
        if code in permissions
    ]
    await db.flush()
    return roles


async def _seed_admin(db: AsyncSession, dept: Dept, roles: dict[str, Role]) -> bool:
    admin = (await db.execute(select(User).where(User.username_normalized == "admin"))).scalars().first()
    if admin is not None:
        if roles["super_admin"] not in admin.roles:
            admin.roles.append(roles["super_admin"])
        return False
    try:
        password_hash = await hash_password_async(settings.admin_password)
    except ValueError as exc:
        # server 启动路径（uvicorn app.main:app）不会走 cli 的 _require_admin_password，
        # 这里把裸 ValueError 换成带操作提示的 RuntimeError，让启动失败可读可操作。
        raise RuntimeError(str(exc)) from exc
    admin = User(
        username="admin",
        username_normalized="admin",
        password_hash=password_hash,
        nickname="系统管理员",
        dept_id=dept.id,
        status="active",
        is_superuser=True,
        # 生产环境强制轮换内置口令；本地开发与测试直接可用
        must_change_password=settings.is_production,
        remark="系统内置管理员",
    )
    admin.roles = [roles["super_admin"]]
    db.add(admin)
    await db.flush()
    return True


async def _seed_dicts(db: AsyncSession) -> None:
    types = {item.code: item for item in (await db.execute(select(DictType))).scalars()}
    items = (await db.execute(select(DictItem))).scalars().all()
    by_type: dict[int, dict[str, DictItem]] = {}
    for item in items:
        by_type.setdefault(item.dict_type_id, {})[item.value] = item
    for definition in DICT_TYPES:
        dict_type = types.get(definition["code"])
        if dict_type is None:
            dict_type = DictType(code=definition["code"], name=definition["name"])
            db.add(dict_type)
            types[definition["code"]] = dict_type
            await db.flush()
        dict_type.name = definition["name"]
        # status 由模型默认值给 active；已有行不动，管理员停用的字典不该被下次 seed 重新打开
        existing_items = by_type.setdefault(dict_type.id, {})
        for item in definition["items"]:
            current = existing_items.get(item["value"])
            if current is None:
                db.add(DictItem(dict_type_id=dict_type.id, **item))
            else:
                current.label = item["label"]
                current.tag_color = item.get("tag_color")
                current.sort = item.get("sort", 0)
    await db.flush()


async def _seed_configs(db: AsyncSession) -> None:
    configs = {item.key: item for item in (await db.execute(select(SystemConfig))).scalars()}
    for item in SYSTEM_CONFIGS:
        config = configs.get(item["key"])
        if config is None:
            payload = {**item, "type": item.get("type", "string"), "built_in": True}
            config = SystemConfig(**payload)
            db.add(config)
        else:
            config.name = item["name"]
            config.type = item.get("type", "string")
            config.group = item["group"]
            # 只允许把参数改成更私密的方向：管理员手动收敛过的 is_public 不该被升级重新公开
            if not item["is_public"]:
                config.is_public = False
            config.built_in = True
    await db.flush()


async def _seed_themes(db: AsyncSession) -> None:
    codes = {item["code"] for item in BUILTIN_THEMES}
    themes = {item.code: item for item in (await db.execute(select(ThemeTemplate))).scalars()}
    for definition in BUILTIN_THEMES:
        theme = themes.get(definition["code"])
        if theme is None:
            theme = ThemeTemplate(code=definition["code"])
            db.add(theme)
            themes[definition["code"]] = theme
        theme.name = definition["name"]
        theme.description = definition["description"]
        theme.config = definition["config"]
        theme.sort = definition["sort"]
        theme.is_builtin = True
    # 内置主题改名或下架后旧行仍会被列表接口返回，这里随种子一并清理
    removed = [theme for code, theme in themes.items() if theme.is_builtin and code not in codes]
    for theme in removed:
        await db.delete(theme)
    if removed:
        active = (
            await db.execute(select(SystemConfig).where(SystemConfig.key == "system.theme"))
        ).scalars().first()
        removed_codes = {theme.code for theme in removed}
        # 只有当前启用的主题确实被下架才回退，否则会把用户自建的主题误重置
        if active and active.value in removed_codes:
            active.value = DEFAULT_THEME_CODE
    await db.flush()


async def _seed_job_templates(db: AsyncSession) -> None:
    templates = {item.code: item for item in (await db.execute(select(JobTemplate))).scalars()}
    for definition in BUILTIN_JOB_TEMPLATES:
        template = templates.get(definition["code"])
        if template is None:
            template = JobTemplate(code=definition["code"])
            db.add(template)
            templates[definition["code"]] = template
        template.name = definition["name"]
        template.description = definition["description"]
        template.job_type = definition["job_type"]
        template.handler_code = definition.get("handler_code")
        template.script_code = definition.get("script_code")
        template.args_json = definition["args_json"]
        template.trigger_type = definition["trigger_type"]
        template.cron_expr = definition.get("cron_expr")
        template.interval_seconds = definition.get("interval_seconds")
        template.timezone = scheduler_timezone()
        template.timeout_seconds = definition["timeout_seconds"]
        template.is_builtin = True
    await db.flush()


async def _seed_jobs(db: AsyncSession) -> None:
    jobs = {item.code: item for item in (await db.execute(select(Job))).scalars()}
    for item in BUILTIN_JOBS:
        job = jobs.get(item["code"])
        if job is None:
            job = Job(**item, timezone=scheduler_timezone(), is_builtin=True)
            db.add(job)
        else:
            job.name = item["name"]
            job.description = item["description"]
            job.is_builtin = True
            # 内置任务的「调度定义」跟随代码：触发器/周期/参数/超时改版后，
            # 指纹变化触发重播，旧库也要跟着升级。status（启停）是运维状态，
            # 保留管理员手动开/关，避免重播把用户显式启用的任务又停掉。
            job.trigger_type = item["trigger_type"]
            job.cron_expr = item.get("cron_expr")
            job.interval_seconds = item.get("interval_seconds")
            job.args_json = item["args_json"]
            job.timeout_seconds = item["timeout_seconds"]
            job.timezone = scheduler_timezone()
    await db.flush()


async def sync_permission_codes(db: AsyncSession) -> None:
    """每次启动只补齐权限码：菜单与其他内置角色留给 seed 版本升级或显式 sync-rbac，
    否则管理员对内置菜单名/排序、审计员权限的自定义会被无声覆盖。"""
    permissions = await _sync_permissions(db)
    super_role = (await db.execute(select(Role).where(Role.code == "super_admin"))).scalars().first()
    if super_role is not None:
        # 只做增量补齐：新增权限码要能立即用，但绝不删减超级管理员已有授权
        granted = {item.code for item in super_role.permissions}
        missing = [permissions[code] for code in sorted(all_permission_codes() - granted) if code in permissions]
        if missing:
            super_role.permissions = [*super_role.permissions, *missing]
    await db.commit()


async def sync_rbac(db: AsyncSession) -> None:
    permissions = await _sync_permissions(db)
    await _sync_menus(db)
    await _seed_roles(db, permissions)
    await db.commit()


async def seed_database(db: AsyncSession) -> bool:
    permissions = await _sync_permissions(db)
    await _sync_menus(db)
    dept = await _seed_dept(db)
    roles = await _seed_roles(db, permissions)
    created_admin = await _seed_admin(db, dept, roles)
    await _seed_dicts(db)
    await _seed_configs(db)
    await _seed_themes(db)
    await _seed_job_templates(db)
    await _seed_jobs(db)

    version = await db.get(BootstrapVersion, "seed")
    if version is None:
        db.add(BootstrapVersion(key="seed", version=SEED_VERSION))
    else:
        if version.version != SEED_VERSION:
            # B-11：版本变化要有日志——旧库从哪个指纹播到哪个指纹，一眼可查
            logger.info("seed fingerprint changed: %s -> %s，重播内置数据", version.version, SEED_VERSION)
        version.version = SEED_VERSION
    await db.commit()

    if created_admin:
        logger.info("initial administrator created: username=admin (password comes from INITIAL_ADMIN_PASSWORD)")
    return created_admin
