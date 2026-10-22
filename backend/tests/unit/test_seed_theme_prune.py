from __future__ import annotations

from sqlalchemy import select

from app.bootstrap.seed import _seed_themes
from app.db.session import session_scope
from app.modules.config.models import SystemConfig
from app.modules.theme.models import ThemeTemplate

RETIRED_CODE = "legacy-finance-dark"


async def test_seed_prunes_retired_builtin_and_repairs_active_theme(client) -> None:
    async with session_scope() as db:
        db.add(
            ThemeTemplate(
                code=RETIRED_CODE,
                name="已下架的内置主题",
                description="升级前遗留",
                config={},
                is_builtin=True,
            )
        )
        active = (
            await db.execute(select(SystemConfig).where(SystemConfig.key == "system.theme"))
        ).scalars().first()
        active.value = RETIRED_CODE
        await db.flush()

    async with session_scope() as db:
        await _seed_themes(db)

    async with session_scope() as db:
        codes = {item.code for item in (await db.execute(select(ThemeTemplate))).scalars()}
        active = (
            await db.execute(select(SystemConfig).where(SystemConfig.key == "system.theme"))
        ).scalars().first()

    assert RETIRED_CODE not in codes
    assert active.value == "classic-blue"
