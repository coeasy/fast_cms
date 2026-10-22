from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.menus import MENU_TREE, iter_menus
from app.bootstrap.models import BootstrapVersion
from app.bootstrap.permissions import ADMIN_PERMISSION, PERMISSION_CODES, PERMISSIONS, READ_ONLY_PERMISSIONS
from app.core.config import settings
from app.core.security import hash_password
from app.core.tz import scheduler_timezone
from app.db.base import utcnow
from app.modules.config.models import DictItem, DictType, SystemConfig
from app.modules.schedule.models import Job, JobTemplate
from app.modules.system.models import Dept, Menu, Permission, Role, User
from app.modules.theme.models import ThemeTemplate
from app.modules.theme.presets import BUILTIN_THEMES, DEFAULT_THEME_CODE

logger = logging.getLogger("fastcms.bootstrap")

# 种子版本号是「内置数据是否需要重新播种」的开关。
# 改了 MENU_TREE / PERMISSIONS / DEFAULT_* 这些内置声明后必须递增它：
# 版本一致时 _ensure_seed() 只补权限码、不碰菜单（避免覆盖管理员的自定义），
# 新增的内置菜单就永远不会出现在侧边栏——入口存在但点不进去，是最难查的一种断链。
SEED_VERSION = "1.8.0"

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
        "args_json": {"days": settings.session_retention_days},
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
        "args_json": {"days": settings.log_retention_days},
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
]

BUILTIN_JOBS: list[dict[str, Any]] = [
    {
        "code": "system.cleanup_sessions",
        "name": "清理过期会话",
        "description": "每天凌晨清理超过保留天数且已过期的登录会话",
        "trigger_type": "cron",
        "cron_expr": "0 3 * * *",
        "args_json": {"days": settings.session_retention_days},
        "status": "enabled",
        "timeout_seconds": 300,
    },
    {
        "code": "system.cleanup_logs",
        "name": "清理过期日志",
        "description": "每天凌晨清理登录、操作、任务与通知发送记录",
        "trigger_type": "cron",
        "cron_expr": "10 3 * * *",
        "args_json": {"days": settings.log_retention_days},
        "status": "enabled",
        "timeout_seconds": 600,
    },
    {
        "code": "system.cleanup_files",
        "name": "清理未引用文件",
        "description": "每周清理未被引用的上传文件，默认暂停以免误删",
        "trigger_type": "cron",
        "cron_expr": "0 4 * * 0",
        "args_json": {"days": settings.file_retention_days},
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
        "description": "投递待发送的 IM 通知，配置好通知渠道后手动启用",
        "trigger_type": "interval",
        "interval_seconds": 60,
        "args_json": {"limit": 20},
        "status": "paused",
        "timeout_seconds": 120,
    },
]


async def _sync_permissions(db: AsyncSession) -> dict[str, Permission]:
    existing = {item.code: item for item in (await db.execute(select(Permission))).scalars()}
    now = utcnow()
    for item in PERMISSIONS:
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
        if code not in PERMISSION_CODES and perm.deprecated_at is None:
            perm.deprecated_at = now
    await db.flush()
    return existing


async def _sync_menus(db: AsyncSession) -> None:
    rows = (await db.execute(select(Menu))).scalars().all()
    existing = {item.path: item for item in rows if item.path}
    flat = list(iter_menus(MENU_TREE))
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
    super_role.permissions = [permissions[code] for code in sorted(PERMISSION_CODES)]

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
    admin = User(
        username="admin",
        username_normalized="admin",
        password_hash=hash_password(settings.admin_password),
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
    await db.flush()


async def sync_permission_codes(db: AsyncSession) -> None:
    """每次启动只补齐权限码：菜单与其他内置角色留给 seed 版本升级或显式 sync-rbac，
    否则管理员对内置菜单名/排序、审计员权限的自定义会被无声覆盖。"""
    permissions = await _sync_permissions(db)
    super_role = (await db.execute(select(Role).where(Role.code == "super_admin"))).scalars().first()
    if super_role is not None:
        # 只做增量补齐：新增权限码要能立即用，但绝不删减超级管理员已有授权
        granted = {item.code for item in super_role.permissions}
        missing = [permissions[code] for code in sorted(PERMISSION_CODES - granted) if code in permissions]
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
        version.version = SEED_VERSION
    await db.commit()

    if created_admin:
        logger.info("initial administrator created: username=admin (password comes from INITIAL_ADMIN_PASSWORD)")
    return created_admin
