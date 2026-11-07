from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext
from app.core.cache import get_cache
from app.core.errors import AppError
from app.core.serializers import iso
from app.kernel.db import commit, log_operation
from app.modules.sys.config.models import SystemConfig
from app.modules.sys.config.service import PUBLIC_CONFIG_KEY
from app.modules.theme.models import ThemeTemplate
from app.modules.theme.presets import DEFAULT_THEME_CODE
from app.modules.theme.schemas import ThemeConfig, ThemeCreate, ThemeUpdate

ACTIVE_THEME_KEY = "system.theme"


def theme_out(item: ThemeTemplate, active_code: str | None = None) -> dict[str, Any]:
    """投影主题。``is_active`` 必须随列表一起回：前端 ``theme.js`` 靠它定位当前主题。

    此前 ThemeOut 没这个字段、服务层也没产出，前端 ``find(i => i.is_active)`` 恒为
    undefined，只能靠再请求一次 ``/system/themes/public`` 兜底——是一次无谓的往返，
    也是「前端读了后端根本不存在的字段」的静默断链。
    """
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "config": item.config or {},
        "is_builtin": item.is_builtin,
        "is_active": active_code is not None and item.code == active_code,
        "sort": item.sort,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


async def _active_code(db: AsyncSession) -> str:
    config = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == ACTIVE_THEME_KEY))
    ).scalars().first()
    return config.value if config and config.value else DEFAULT_THEME_CODE


async def _out(db: AsyncSession, item: ThemeTemplate) -> dict[str, Any]:
    """单条响应也带 is_active，避免详情/创建/启用与列表口径不一致。"""
    return theme_out(item, await _active_code(db))


async def list_themes(db: AsyncSession) -> list[dict[str, Any]]:
    items = (
        (await db.execute(select(ThemeTemplate).order_by(ThemeTemplate.sort, ThemeTemplate.id)))
        .scalars()
        .all()
    )
    active_code = await _active_code(db)
    if items and not any(item.code == active_code for item in items):
        # 配置指向的主题已被删掉时与 public_theme 保持同一回退口径
        active_code = items[0].code
    return [theme_out(item, active_code) for item in items]


async def public_theme(db: AsyncSession) -> dict[str, Any]:
    items = await list_themes(db)
    active = next((item for item in items if item["is_active"]), None)
    return {"active": active, "items": items}


async def get_theme(db: AsyncSession, theme_id: int) -> dict[str, Any]:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", status_code=404)
    return await _out(db, item)


async def _log(db: AsyncSession, ctx: AuthContext, action: str, theme: ThemeTemplate) -> None:
    # B-1 ④：统一走 kernel.db.log_operation，删除本地重复的 OperationLog 写入，
    # 并获得真实 duration_ms（由请求上下文推导）。
    await log_operation(
        db,
        ctx,
        module="theme",
        action=action,
        method="POST",
        path=f"/system/themes/{theme.id}",
        detail=json.dumps({"code": theme.code, "name": theme.name}, ensure_ascii=False),
    )


async def create_theme(db: AsyncSession, ctx: AuthContext, data: ThemeCreate) -> dict[str, Any]:
    exists = (
        await db.execute(select(ThemeTemplate.id).where(ThemeTemplate.code == data.code))
    ).scalar_one_or_none()
    if exists:
        raise AppError("THEME_CODE_EXISTS", status_code=409)
    item = ThemeTemplate(
        code=data.code,
        name=data.name,
        description=data.description,
        config=data.config.model_dump(by_alias=True),
        sort=data.sort,
        is_builtin=False,
    )
    db.add(item)
    await db.flush()
    await _log(db, ctx, "创建主题模板", item)
    await commit(db, conflict_code="THEME_CODE_EXISTS")
    await db.refresh(item)
    return await _out(db, item)


async def update_theme(
    db: AsyncSession, ctx: AuthContext, theme_id: int, data: ThemeUpdate
) -> dict[str, Any]:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", status_code=404)
    if item.is_builtin:
        raise AppError("THEME_BUILTIN", "内置主题不可修改，可复制为自定义主题", 409)
    payload = data.model_dump(exclude_unset=True)
    if "config" in payload and payload["config"] is not None:
        payload["config"] = ThemeConfig.model_validate(payload["config"]).model_dump(by_alias=True)
    for field, value in payload.items():
        setattr(item, field, value)
    await _log(db, ctx, "更新主题模板", item)
    await commit(db, conflict_code="THEME_CODE_EXISTS")
    await db.refresh(item)
    return await _out(db, item)


async def delete_theme(db: AsyncSession, ctx: AuthContext, theme_id: int) -> None:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", status_code=404)
    if item.is_builtin:
        raise AppError("THEME_BUILTIN", "内置主题不可删除", 409)
    if item.code == await _active_code(db):
        raise AppError("THEME_ACTIVE", status_code=409)
    await _log(db, ctx, "删除主题模板", item)
    await db.delete(item)
    await commit(db)


async def activate_theme(db: AsyncSession, ctx: AuthContext, theme_id: int) -> dict[str, Any]:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", status_code=404)
    config = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == ACTIVE_THEME_KEY))
    ).scalars().first()
    if config is None:
        config = SystemConfig(
            key=ACTIVE_THEME_KEY,
            name="当前主题",
            value=item.code,
            type="string",
            group="appearance",
            is_public=True,
            built_in=True,
        )
        db.add(config)
    else:
        config.value = item.code
    await _log(db, ctx, "启用主题模板", item)
    await commit(db)
    await get_cache().delete(PUBLIC_CONFIG_KEY)
    return await _out(db, item)
