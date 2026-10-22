from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import success
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.config import service
from app.modules.config.schemas import (
    ConfigCreate,
    ConfigUpdate,
    DictItemCreate,
    DictItemUpdate,
    DictTypeCreate,
    DictTypeUpdate,
)

public_router = APIRouter(prefix="/system", tags=["系统配置"])
router = protected_router(
    prefix="/system",
    tags=["系统配置"],
    dependencies=[Depends(require_admin)],
)


@public_router.get("/public-config", summary="公开配置")
async def public_config(db: AsyncSession = Depends(get_db)):
    return success(await service.public_config(db))


@router.get("/dict-types", summary="字典类型列表", permission="system:dict:list")
async def list_dict_types(
    keyword: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(active|disabled)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_dict_types(db, keyword=keyword, status=status))


@router.get("/dict-types/{type_id}", summary="字典类型详情", permission="system:dict:list")
async def get_dict_type(
    type_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_dict_type(db, type_id))


@router.post("/dict-types", summary="创建字典类型", permission="system:dict:create")
async def create_dict_type(
    data: DictTypeCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_dict_type(db, data), "字典类型已创建")


@router.put("/dict-types/{type_id}", summary="更新字典类型", permission="system:dict:update")
async def update_dict_type(
    type_id: int,
    data: DictTypeUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_dict_type(db, type_id, data), "字典类型已更新")


@router.delete("/dict-types/{type_id}", summary="删除字典类型", permission="system:dict:delete")
async def delete_dict_type(
    type_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_dict_type(db, type_id)
    return success(None, "字典类型已删除")


@router.get("/dict-types/{type_id}/items", summary="字典项列表", permission="system:dict:list")
async def list_dict_items(
    type_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_dict_items(db, type_id))


@router.post("/dict-types/{type_id}/items", summary="创建字典项", permission="system:dict:create")
async def create_dict_item(
    type_id: int,
    data: DictItemCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_dict_item(db, type_id, data), "字典项已创建")


@router.put("/dict-items/{item_id}", summary="更新字典项", permission="system:dict:update")
async def update_dict_item(
    item_id: int,
    data: DictItemUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_dict_item(db, item_id, data), "字典项已更新")


@router.delete("/dict-items/{item_id}", summary="删除字典项", permission="system:dict:delete")
async def delete_dict_item(
    item_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_dict_item(db, item_id)
    return success(None, "字典项已删除")


@router.get("/dicts/{code}", summary="按编码读取字典", permission="system:dict:read")
async def read_dict(
    code: str,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.read_dict(db, code))


@router.get("/configs", summary="系统参数列表", permission="system:config:list")
async def list_configs(
    keyword: str | None = Query(default=None, max_length=64),
    group: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_configs(db, keyword=keyword, group=group))


@router.post("/configs", summary="创建系统参数", permission="system:config:create")
async def create_config(
    data: ConfigCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_config(db, data), "参数已创建")


@router.put("/configs/{config_id}", summary="更新系统参数", permission="system:config:update")
async def update_config(
    config_id: int,
    data: ConfigUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_config(db, config_id, data), "参数已更新")


@router.delete("/configs/{config_id}", summary="删除系统参数", permission="system:config:delete")
async def delete_config(
    config_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_config(db, config_id)
    return success(None, "参数已删除")
