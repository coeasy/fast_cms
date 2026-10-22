from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import success
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.theme import service
from app.modules.theme.schemas import ThemeCreate, ThemeUpdate

public_router = APIRouter(prefix="/system", tags=["主题模板"])
router = protected_router(
    prefix="/system",
    tags=["主题模板"],
    dependencies=[Depends(require_admin)],
)


@public_router.get("/themes/public", summary="公开主题模板")
async def public_themes(db: AsyncSession = Depends(get_db)):
    return success(await service.public_theme(db))


@router.get("/themes", summary="主题模板列表", permission="system:theme:list")
async def list_themes(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_themes(db))


@router.get("/themes/{theme_id}", summary="主题模板详情", permission="system:theme:list")
async def get_theme(
    theme_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_theme(db, theme_id))


@router.post("/themes", summary="创建主题模板", permission="system:theme:create")
async def create_theme(
    data: ThemeCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_theme(db, ctx, data), "主题模板已创建")


@router.put("/themes/{theme_id}", summary="更新主题模板", permission="system:theme:update")
async def update_theme(
    theme_id: int,
    data: ThemeUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_theme(db, ctx, theme_id, data), "主题模板已更新")


@router.delete("/themes/{theme_id}", summary="删除主题模板", permission="system:theme:delete")
async def delete_theme(
    theme_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_theme(db, ctx, theme_id)
    return success(None, "主题模板已删除")


@router.post("/themes/{theme_id}/activate", summary="启用主题模板", permission="system:theme:update")
async def activate_theme(
    theme_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.activate_theme(db, ctx, theme_id), "主题模板已启用")
