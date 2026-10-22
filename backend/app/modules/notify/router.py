from __future__ import annotations

import re

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import success
from app.core.pagination import PageParams, page_data
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.notify import service
from app.modules.notify.models import CHANNEL_TYPES, RECORD_EVENTS, RECORD_STATUSES
from app.modules.notify.schemas import NotifyChannelCreate, NotifyChannelUpdate

router = protected_router(
    prefix="/notify",
    tags=["消息通知"],
    dependencies=[Depends(require_admin)],
)


def _enum_pattern(values: tuple[str, ...]) -> str:
    return "^(" + "|".join(re.escape(value) for value in values) + ")$"


@router.get("/channels", summary="通知渠道列表", permission="notify:channel:list")
async def list_channels(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=64),
    channel_type: str | None = Query(default=None, pattern=_enum_pattern(CHANNEL_TYPES)),
    status: str | None = Query(default=None, pattern="^(enabled|disabled)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_channels(
        db, params, keyword=keyword, channel_type=channel_type, status=status
    )
    return success(page_data(items, total, params))


@router.get("/channels/{channel_id}", summary="通知渠道详情", permission="notify:channel:list")
async def get_channel(
    channel_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_channel(db, channel_id))


@router.post("/channels", summary="创建通知渠道", permission="notify:channel:create")
async def create_channel(
    data: NotifyChannelCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_channel(db, ctx, data), "通知渠道已创建")


@router.put("/channels/{channel_id}", summary="更新通知渠道", permission="notify:channel:update")
async def update_channel(
    channel_id: int,
    data: NotifyChannelUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_channel(db, ctx, channel_id, data), "通知渠道已更新")


@router.delete("/channels/{channel_id}", summary="删除通知渠道", permission="notify:channel:delete")
async def delete_channel(
    channel_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_channel(db, channel_id)
    return success(None, "通知渠道已删除")


@router.post("/channels/{channel_id}/test", summary="测试通知渠道", permission="notify:channel:test")
async def test_channel(
    channel_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.test_channel(db, channel_id), "测试消息已投递")


@router.get("/records", summary="通知发送记录", permission="notify:record:list")
async def list_records(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=128),
    event: str | None = Query(default=None, pattern=_enum_pattern(RECORD_EVENTS)),
    status: str | None = Query(default=None, pattern=_enum_pattern(RECORD_STATUSES)),
    channel_id: int | None = Query(default=None, ge=1),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_records(
        db, params, keyword=keyword, event=event, status=status, channel_id=channel_id
    )
    return success(page_data(items, total, params))


@router.get("/records/{record_id}", summary="通知发送记录详情", permission="notify:record:list")
async def get_record(
    record_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_record(db, record_id))


@router.post("/records/{record_id}/retry", summary="重试通知发送", permission="notify:record:retry")
async def retry_record(
    record_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.retry_record(db, record_id), "已重新投递")


@router.delete("/records", summary="清理通知发送记录", permission="notify:record:delete")
async def cleanup_records(
    before_days: int = Query(default=30, ge=1, le=3650),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    deleted = await service.cleanup_records(db, before_days)
    return success({"deleted": deleted}, f"已清理 {deleted} 条发送记录")
