from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext
from app.core.cache import get_cache
from app.core.config import api_path
from app.core.errors import AppError
from app.core.serializers import iso
from app.modules.audit.models import OperationLog
from app.modules.config.models import SystemConfig
from app.modules.config.service import PUBLIC_CONFIG_KEY
from app.modules.theme.models import ThemeTemplate
from app.modules.theme.presets import DEFAULT_THEME_CODE
from app.modules.theme.schemas import ThemeConfig, ThemeCreate, ThemeUpdate

ACTIVE_THEME_KEY = "system.theme"


def theme_out(item: ThemeTemplate) -> dict[str, Any]:
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "config": item.config or {},
        "is_builtin": item.is_builtin,
        "sort": item.sort,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


async def _active_code(db: AsyncSession) -> str:
    config = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == ACTIVE_THEME_KEY))
    ).scalars().first()
    return config.value if config and config.value else DEFAULT_THEME_CODE


async def list_themes(db: AsyncSession) -> list[dict[str, Any]]:
    items = (
        (await db.execute(select(ThemeTemplate).order_by(ThemeTemplate.sort, ThemeTemplate.id)))
        .scalars()
        .all()
    )
    return [theme_out(item) for item in items]


async def public_theme(db: AsyncSession) -> dict[str, Any]:
    items = await list_themes(db)
    active_code = await _active_code(db)
    active = next((item for item in items if item["code"] == active_code), None)
    if active is None and items:
        active = items[0]
    return {"active": active, "items": items}


async def get_theme(db: AsyncSession, theme_id: int) -> dict[str, Any]:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", "主题模板不存在", 404)
    return theme_out(item)


def _log(db: AsyncSession, ctx: AuthContext, action: str, theme: ThemeTemplate) -> None:
    db.add(
        OperationLog(
            user_id=ctx.user.id,
            username=ctx.user.username,
            module="theme",
            action=action,
            method="POST",
            path=api_path(f"/system/themes/{theme.id}"),
            status_code=200,
            success=True,
            duration_ms=0,
            detail=json.dumps({"code": theme.code, "name": theme.name}, ensure_ascii=False),
        )
    )


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppError("THEME_CODE_EXISTS", "主题编码已存在", 409) from exc


async def create_theme(db: AsyncSession, ctx: AuthContext, data: ThemeCreate) -> dict[str, Any]:
    exists = (
        await db.execute(select(ThemeTemplate.id).where(ThemeTemplate.code == data.code))
    ).scalar_one_or_none()
    if exists:
        raise AppError("THEME_CODE_EXISTS", "主题编码已存在", 409)
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
    _log(db, ctx, "创建主题模板", item)
    await _commit(db)
    await db.refresh(item)
    return theme_out(item)


async def update_theme(
    db: AsyncSession, ctx: AuthContext, theme_id: int, data: ThemeUpdate
) -> dict[str, Any]:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", "主题模板不存在", 404)
    if item.is_builtin:
        raise AppError("THEME_BUILTIN", "内置主题不可修改，可复制为自定义主题", 409)
    payload = data.model_dump(exclude_unset=True)
    if "config" in payload and payload["config"] is not None:
        payload["config"] = ThemeConfig.model_validate(payload["config"]).model_dump(by_alias=True)
    for field, value in payload.items():
        setattr(item, field, value)
    _log(db, ctx, "更新主题模板", item)
    await _commit(db)
    await db.refresh(item)
    return theme_out(item)


async def delete_theme(db: AsyncSession, ctx: AuthContext, theme_id: int) -> None:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", "主题模板不存在", 404)
    if item.is_builtin:
        raise AppError("THEME_BUILTIN", "内置主题不可删除", 409)
    if item.code == await _active_code(db):
        raise AppError("THEME_ACTIVE", "当前启用中的主题不可删除", 409)
    _log(db, ctx, "删除主题模板", item)
    await db.delete(item)
    await db.commit()


async def activate_theme(db: AsyncSession, ctx: AuthContext, theme_id: int) -> dict[str, Any]:
    item = await db.get(ThemeTemplate, theme_id)
    if item is None:
        raise AppError("THEME_NOT_FOUND", "主题模板不存在", 404)
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
    _log(db, ctx, "启用主题模板", item)
    await db.commit()
    await get_cache().delete(PUBLIC_CONFIG_KEY)
    return theme_out(item)
