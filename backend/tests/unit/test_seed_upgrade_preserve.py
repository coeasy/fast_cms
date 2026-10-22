from __future__ import annotations

from sqlalchemy import select

from app.bootstrap.seed import _seed_configs, _seed_dicts, _seed_themes, _sync_menus, sync_permission_codes
from app.db.session import session_scope
from app.modules.config.models import DictItem, DictType, SystemConfig
from app.modules.system.models import Menu, Permission, Role
from app.modules.theme.models import ThemeTemplate

RETIRED_PATH = "/legacy-retired-page"
PROBE_PATH = "/content/notice"


async def test_seed_keeps_operator_menu_choices_and_prunes_retired_builtin(client) -> None:
    async with session_scope() as db:
        hidden = (await db.execute(select(Menu).where(Menu.path == PROBE_PATH))).scalars().one()
        hidden.visible = False
        hidden.status = "disabled"
        orphan_parent = Menu(
            path=RETIRED_PATH,
            name="已下架的内置菜单",
            type="directory",
            sort=99,
            visible=True,
            status="active",
            is_builtin=True,
        )
        childless = Menu(
            path=f"{RETIRED_PATH}-leaf",
            name="已下架的空菜单",
            type="menu",
            component_key="dashboard",
            sort=1,
            visible=True,
            status="active",
            is_builtin=True,
        )
        db.add_all([orphan_parent, childless])
        await db.flush()
        # 管理员把自定义菜单挂在被下架的内置目录下：parent_id 是 ON DELETE CASCADE，直接删会连带丢失子树
        db.add(
            Menu(
                path="/custom-under-retired",
                name="管理员自建子菜单",
                type="menu",
                component_key="dashboard",
                parent_id=orphan_parent.id,
                sort=1,
                visible=True,
                status="active",
                is_builtin=False,
            )
        )
        await db.flush()

    try:
        async with session_scope() as db:
            await _sync_menus(db)

        async with session_scope() as db:
            rows = {item.path: item for item in (await db.execute(select(Menu))).scalars()}

        assert f"{RETIRED_PATH}-leaf" not in rows, "无子节点的遗留内置菜单必须清理，否则点进去直接 404"
        assert RETIRED_PATH in rows and rows[RETIRED_PATH].status == "disabled", "有子节点时只能停用，不能级联删掉自定义菜单"
        assert "/custom-under-retired" in rows, "管理员自建菜单不能因为父级下架而被连带删除"
        kept = rows[PROBE_PATH]
        assert kept.visible is False and kept.status == "disabled", "管理员的隐藏/停用选择不能被 seed 恢复"
    finally:
        async with session_scope() as db:
            for path in ("/custom-under-retired", RETIRED_PATH):
                stale = (await db.execute(select(Menu).where(Menu.path == path))).scalars().all()
                for item in reversed(stale):
                    await db.delete(item)
            restored = (await db.execute(select(Menu).where(Menu.path == PROBE_PATH))).scalars().one()
            restored.visible = True
            restored.status = "active"
            await db.flush()


async def test_seed_does_not_reexpose_private_config_or_disabled_dict(client) -> None:
    async with session_scope() as db:
        config = (
            await db.execute(select(SystemConfig).where(SystemConfig.key == "system.icp"))
        ).scalars().one()
        config.is_public = False
        dict_type = (
            await db.execute(select(DictType).where(DictType.code == "yes_no"))
        ).scalars().one()
        dict_type.status = "disabled"
        disabled_item = (
            await db.execute(select(DictItem).where(DictItem.dict_type_id == dict_type.id).limit(1))
        ).scalars().one()
        disabled_item.status = "disabled"
        item_id = disabled_item.id
        await db.flush()

    async with session_scope() as db:
        await _seed_configs(db)
        await _seed_dicts(db)

    try:
        async with session_scope() as db:
            config = (
                await db.execute(select(SystemConfig).where(SystemConfig.key == "system.icp"))
            ).scalars().first()
            dict_type = (
                await db.execute(select(DictType).where(DictType.code == "yes_no"))
            ).scalars().first()
            probed = (
                await db.execute(select(DictItem).where(DictItem.id == item_id))
            ).scalars().one()

        # 种子只允许把参数收紧成私密，不能把管理员收敛过的 is_public 重新公开
        assert config.is_public is False
        assert dict_type.name == "是否"
        assert dict_type.status == "disabled", "管理员停用的内置字典不该被种子重新启用"
        assert probed.status == "disabled", "种子只同步文案，不覆盖字典项的启停状态"
    finally:
        async with session_scope() as db:
            (
                await db.execute(select(SystemConfig).where(SystemConfig.key == "system.icp"))
            ).scalars().one().is_public = True
            yes_no = (
                await db.execute(select(DictType).where(DictType.code == "yes_no"))
            ).scalars().one()
            yes_no.status = "active"
            (
                await db.execute(select(DictItem).where(DictItem.id == item_id))
            ).scalars().one().status = "active"
            await db.flush()


async def test_boot_sync_only_adds_permissions_and_keeps_customizations(client) -> None:
    # 选一个非只读权限码：审计员等内置角色默认没有它，删掉不会波及其他角色
    probe_code = "tool:codegen:generate"
    async with session_scope() as db:
        menu = (await db.execute(select(Menu).where(Menu.path == PROBE_PATH))).scalars().one()
        menu.name = "自定义公告标题"
        role = (await db.execute(select(Role).where(Role.code == "super_admin"))).scalars().one()
        role.permissions = [item for item in role.permissions if item.code != probe_code]
        permission = (await db.execute(select(Permission).where(Permission.code == probe_code))).scalars().first()
        if permission is not None:
            await db.delete(permission)
        await db.flush()

    try:
        async with session_scope() as db:
            await sync_permission_codes(db)

        async with session_scope() as db:
            menu = (await db.execute(select(Menu).where(Menu.path == PROBE_PATH))).scalars().one()
            role = (await db.execute(select(Role).where(Role.code == "super_admin"))).scalars().one()
            restored = (
                await db.execute(select(Permission).where(Permission.code == probe_code))
            ).scalars().first()

        assert restored is not None, "启动时必须补齐缺失的权限码"
        assert probe_code in {item.code for item in role.permissions}
        assert menu.name == "自定义公告标题", "启动同步不能覆盖管理员对内置菜单的改名"
    finally:
        async with session_scope() as db:
            menu = (await db.execute(select(Menu).where(Menu.path == PROBE_PATH))).scalars().one()
            menu.name = "公告管理"
            await db.flush()


async def test_seed_keeps_active_custom_theme(client) -> None:
    async with session_scope() as db:
        db.add(
            ThemeTemplate(
                code="custom-pro",
                name="自建主题",
                description="管理员创建",
                config={},
                is_builtin=False,
            )
        )
        active = (
            await db.execute(select(SystemConfig).where(SystemConfig.key == "system.theme"))
        ).scalars().first()
        active.value = "custom-pro"
        await db.flush()

    async with session_scope() as db:
        await _seed_themes(db)

    try:
        async with session_scope() as db:
            codes = {item.code for item in (await db.execute(select(ThemeTemplate))).scalars()}
            active = (
                await db.execute(select(SystemConfig).where(SystemConfig.key == "system.theme"))
            ).scalars().first()

        assert "custom-pro" in codes, "下架内置主题不能顺带删除用户自建主题"
        assert active.value == "custom-pro", "只有当前启用的主题被下架才需要回退默认值"
    finally:
        async with session_scope() as db:
            for theme in (await db.execute(select(ThemeTemplate).where(ThemeTemplate.code == "custom-pro"))).scalars():
                await db.delete(theme)
            active = (
                await db.execute(select(SystemConfig).where(SystemConfig.key == "system.theme"))
            ).scalars().first()
            active.value = "classic-blue"
            await db.flush()
