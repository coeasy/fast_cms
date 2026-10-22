from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext
from app.core.config import settings
from app.core.errors import AppError
from app.core.pagination import PageParams, paginate
from app.core.serializers import iso
from app.db.base import rowcount, utcnow
from app.db.session import session_scope
from app.modules.notify import channels
from app.modules.notify.models import NotifyChannel, NotifyRecord
from app.modules.notify.schemas import NotifyChannelCreate, NotifyChannelUpdate

CONTENT_LIMIT = 4000
TITLE_LIMIT = 128
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 1800
# 发送中但长时间没有回写的记录视为投递进程被杀，重新排队。
SENDING_STALE_SECONDS = 300
TERMINAL_STATUSES = ("success", "failed", "skipped")


def channel_out(item: NotifyChannel) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "channel_type": item.channel_type,
        "webhook_url_masked": channels.mask_webhook_url(item.webhook_url),
        "has_secret": bool(item.secret),
        "events": list(item.events or []),
        "status": item.status,
        "remark": item.remark,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


def record_out(item: NotifyRecord, *, include_detail: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id,
        "channel_id": item.channel_id,
        "channel_name": item.channel_name,
        "channel_type": item.channel_type,
        "event": item.event,
        "dedupe_key": item.dedupe_key,
        "title": item.title,
        "status": item.status,
        "attempts": item.attempts,
        "last_error": item.last_error,
        "response_status": item.response_status,
        "sent_at": iso(item.sent_at),
        "next_retry_at": iso(item.next_retry_at),
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
    if include_detail:
        data["content"] = item.content
    return data


async def list_channels(
    db: AsyncSession,
    params: PageParams,
    *,
    keyword: str | None = None,
    channel_type: str | None = None,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    statement = select(NotifyChannel)
    if keyword:
        like = f"%{keyword.strip()}%"
        statement = statement.where(or_(NotifyChannel.name.like(like), NotifyChannel.remark.like(like)))
    if channel_type:
        statement = statement.where(NotifyChannel.channel_type == channel_type)
    if status:
        statement = statement.where(NotifyChannel.status == status)
    rows, total = await paginate(db, statement.order_by(NotifyChannel.id), params)
    return [channel_out(item) for item in rows], total


async def get_channel(db: AsyncSession, channel_id: int) -> dict[str, Any]:
    item = await db.get(NotifyChannel, channel_id)
    if item is None:
        raise AppError("NOTIFY_CHANNEL_NOT_FOUND", "通知渠道不存在", 404)
    return channel_out(item)


async def create_channel(
    db: AsyncSession, ctx: AuthContext, data: NotifyChannelCreate
) -> dict[str, Any]:
    exists = (
        await db.execute(select(NotifyChannel.id).where(NotifyChannel.name == data.name))
    ).scalar_one_or_none()
    if exists:
        raise AppError("NOTIFY_CHANNEL_EXISTS", "同名通知渠道已存在", 409)
    item = NotifyChannel(
        name=data.name,
        channel_type=data.channel_type,
        webhook_url=channels.validate_webhook_url(data.webhook_url),
        secret=(data.secret or "").strip() or None,
        events=list(data.events),
        status=data.status,
        remark=data.remark,
        created_by=ctx.user.id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return channel_out(item)


async def update_channel(
    db: AsyncSession, ctx: AuthContext, channel_id: int, data: NotifyChannelUpdate
) -> dict[str, Any]:
    item = await db.get(NotifyChannel, channel_id)
    if item is None:
        raise AppError("NOTIFY_CHANNEL_NOT_FOUND", "通知渠道不存在", 404)
    payload = data.model_dump(exclude_unset=True)
    if payload.get("webhook_url"):
        item.webhook_url = channels.validate_webhook_url(payload["webhook_url"])
    if "secret" in payload:
        # 传空串表示清除密钥，不传该字段表示保留原值，避免脱敏回显后被覆盖。
        cleaned = (payload["secret"] or "").strip()
        item.secret = cleaned or None
    if payload.get("name"):
        duplicate = (
            await db.execute(
                select(NotifyChannel.id).where(
                    NotifyChannel.name == payload["name"], NotifyChannel.id != channel_id
                )
            )
        ).scalar_one_or_none()
        if duplicate:
            raise AppError("NOTIFY_CHANNEL_EXISTS", "同名通知渠道已存在", 409)
        item.name = payload["name"]
    if payload.get("events"):
        item.events = list(payload["events"])
    if payload.get("status"):
        item.status = payload["status"]
    if "remark" in payload:
        item.remark = payload["remark"]
    item.updated_by = ctx.user.id
    await db.commit()
    await db.refresh(item)
    return channel_out(item)


async def delete_channel(db: AsyncSession, channel_id: int) -> None:
    item = await db.get(NotifyChannel, channel_id)
    if item is None:
        raise AppError("NOTIFY_CHANNEL_NOT_FOUND", "通知渠道不存在", 404)
    await db.delete(item)
    await db.commit()


async def list_records(
    db: AsyncSession,
    params: PageParams,
    *,
    keyword: str | None = None,
    event: str | None = None,
    status: str | None = None,
    channel_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    statement = select(NotifyRecord)
    if keyword:
        like = f"%{keyword.strip()}%"
        statement = statement.where(
            or_(
                NotifyRecord.title.like(like),
                NotifyRecord.channel_name.like(like),
                NotifyRecord.dedupe_key.like(like),
            )
        )
    if event:
        statement = statement.where(NotifyRecord.event == event)
    if status:
        statement = statement.where(NotifyRecord.status == status)
    if channel_id:
        statement = statement.where(NotifyRecord.channel_id == channel_id)
    rows, total = await paginate(db, statement.order_by(NotifyRecord.id.desc()), params)
    return [record_out(item) for item in rows], total


async def get_record(db: AsyncSession, record_id: int) -> dict[str, Any]:
    item = await db.get(NotifyRecord, record_id)
    if item is None:
        raise AppError("NOTIFY_RECORD_NOT_FOUND", "发送记录不存在", 404)
    return record_out(item, include_detail=True)


async def _queue(
    db: AsyncSession,
    channel: NotifyChannel,
    *,
    event: str,
    dedupe_key: str,
    title: str,
    content: str,
    status: str = "pending",
    attempts: int = 0,
    next_retry_at: datetime | None = None,
) -> NotifyRecord | None:
    record = NotifyRecord(
        channel_id=channel.id,
        channel_name=channel.name,
        channel_type=channel.channel_type,
        event=event,
        dedupe_key=dedupe_key,
        title=title[:TITLE_LIMIT],
        content=(content or "")[:CONTENT_LIMIT],
        status=status,
        attempts=attempts,
        next_retry_at=next_retry_at or utcnow(),
    )
    try:
        # 触发点可能被重放或在多实例下并发命中，靠唯一约束兜底去重。
        async with db.begin_nested():
            db.add(record)
            await db.flush()
    except IntegrityError:
        return None
    return record


async def enqueue(
    db: AsyncSession,
    *,
    event: str,
    dedupe_key: str,
    title: str,
    content: str,
) -> int:
    """Queue one record per subscribed channel inside the caller's transaction."""
    subscribed = (
        (
            await db.execute(
                select(NotifyChannel).where(NotifyChannel.status == "enabled").order_by(NotifyChannel.id)
            )
        )
        .scalars()
        .all()
    )
    queued = 0
    for channel in subscribed:
        if event not in (channel.events or []):
            continue
        if await _queue(db, channel, event=event, dedupe_key=dedupe_key, title=title, content=content):
            queued += 1
    return queued


async def emit(*, event: str, dedupe_key: str, title: str, content: str) -> int:
    """Queue a notification in its own transaction, used by the trigger points."""
    if not settings.notify_enabled:
        return 0
    async with session_scope() as db:
        return await enqueue(db, event=event, dedupe_key=dedupe_key, title=title, content=content)


async def test_channel(db: AsyncSession, channel_id: int) -> dict[str, Any]:
    channel = await db.get(NotifyChannel, channel_id)
    if channel is None:
        raise AppError("NOTIFY_CHANNEL_NOT_FOUND", "通知渠道不存在", 404)
    record = await _queue(
        db,
        channel,
        event="test",
        dedupe_key=uuid.uuid4().hex,
        title="Fast CMS 通知渠道测试",
        content=f"渠道 {channel.name} 配置可用，收到该消息说明推送链路正常。",
        status="sending",
        # 测试消息只做一次同步投递，失败不再进入自动重试队列。
        attempts=settings.notify_max_attempts,
    )
    if record is None:
        raise AppError("NOTIFY_RECORD_CONFLICT", "测试消息排队失败，请稍后重试", 409)
    record_id = int(record.id)
    await db.commit()
    await _deliver(record_id)
    await db.refresh(record)
    return record_out(record, include_detail=True)


async def retry_record(db: AsyncSession, record_id: int) -> dict[str, Any]:
    item = await db.get(NotifyRecord, record_id)
    if item is None:
        raise AppError("NOTIFY_RECORD_NOT_FOUND", "发送记录不存在", 404)
    if item.status == "success":
        raise AppError("NOTIFY_RECORD_SENT", "该记录已发送成功，无需重试", 409)
    if item.status == "sending":
        # 投递中的记录再点一次会向同一个 webhook 重复推送
        raise AppError("NOTIFY_RECORD_SENDING", "该记录正在投递中，请稍后再试", 409)
    item.status = "sending"
    item.attempts += 1
    item.last_error = None
    await db.commit()
    await _deliver(record_id)
    await db.refresh(item)
    return record_out(item, include_detail=True)


def _backoff(attempts: int) -> timedelta:
    seconds = min(BACKOFF_BASE_SECONDS * 2 ** max(attempts - 1, 0), BACKOFF_MAX_SECONDS)
    return timedelta(seconds=seconds)


async def _deliver(record_id: int) -> None:
    """Send one already-claimed record and write back its result."""
    async with session_scope() as db:
        record = await db.get(NotifyRecord, record_id)
        if record is None or record.status != "sending":
            return
        claimed_attempts = record.attempts
        channel = await db.get(NotifyChannel, record.channel_id) if record.channel_id else None
        if channel is None or channel.status != "enabled":
            record.status = "skipped"
            record.last_error = "渠道已删除或已停用，消息未发送"
            return
        channel_type = channel.channel_type
        webhook_url = channel.webhook_url
        secret = channel.secret
        title = record.title
        content = record.content
        event = record.event
        dedupe_key = record.dedupe_key

    try:
        result = await channels.send_markdown(
            channel_type=channel_type,
            webhook_url=webhook_url,
            secret=secret,
            title=title,
            content=content,
            event=event,
            dedupe_key=dedupe_key,
        )
    except Exception as exc:
        result = channels.SendResult(False, f"{type(exc).__name__}: {exc}"[:512], None)

    async with session_scope() as db:
        record = await db.get(NotifyRecord, record_id)
        if record is None:
            return
        # attempts 由 _claim 原子递增，等价于本次投递的版本号：
        # 僵死回收把记录重新排队并被别的进程抢走后，旧投递必须放弃回写，否则覆盖新投递的状态
        if record.status != "sending" or record.attempts != claimed_attempts:
            return
        record.response_status = result.status_code
        if result.ok:
            record.status = "success"
            record.sent_at = utcnow()
            record.last_error = None
            return
        record.last_error = (result.error or "未知错误")[:512]
        if record.attempts >= settings.notify_max_attempts:
            record.status = "failed"
        else:
            record.status = "pending"
            record.next_retry_at = utcnow() + _backoff(record.attempts)


async def _claim(limit: int) -> list[int]:
    now = utcnow()
    async with session_scope() as db:
        recovered = await db.execute(
            update(NotifyRecord)
            .where(
                NotifyRecord.status == "sending",
                NotifyRecord.updated_at < now - timedelta(seconds=SENDING_STALE_SECONDS),
            )
            .values(status="pending", last_error="投递进程中断，记录已重新排队")
        )
        if rowcount(recovered):
            # 僵死记录本轮只回收，下一轮再抢，避免与原进程仍在发送的消息重复推送。
            return []
        ids = (
            (
                await db.execute(
                    select(NotifyRecord.id)
                    .where(NotifyRecord.status == "pending", NotifyRecord.next_retry_at <= now)
                    .order_by(NotifyRecord.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[int] = []
        for record_id in ids:
            # 逐行条件更新等于原子抢占，多实例同时投递也只有一方拿到。
            result = await db.execute(
                update(NotifyRecord)
                .where(NotifyRecord.id == record_id, NotifyRecord.status == "pending")
                .values(status="sending", attempts=NotifyRecord.attempts + 1, updated_at=now)
            )
            if rowcount(result):
                claimed.append(int(record_id))
    return claimed


async def dispatch(*, limit: int | None = None) -> dict[str, int]:
    """Send queued records; safe to run from several workers at once."""
    if not settings.notify_enabled:
        return {"claimed": 0, "success": 0, "pending": 0, "failed": 0, "skipped": 0}
    claimed = await _claim(int(limit or settings.notify_batch_size))
    for record_id in claimed:
        await _deliver(record_id)
    counts = {"claimed": len(claimed), "success": 0, "pending": 0, "failed": 0, "skipped": 0}
    if not claimed:
        return counts
    async with session_scope() as db:
        statuses = (
            (await db.execute(select(NotifyRecord.status).where(NotifyRecord.id.in_(claimed))))
            .scalars()
            .all()
        )
    for status in statuses:
        if status in counts:
            counts[status] += 1
    return counts


async def cleanup_records(db: AsyncSession, days: int) -> int:
    cutoff = utcnow() - timedelta(days=days)
    # 只清终态记录：pending/sending 还在投递队列里，删掉等于静默丢消息
    result = await db.execute(
        delete(NotifyRecord).where(
            NotifyRecord.created_at < cutoff,
            NotifyRecord.status.in_(TERMINAL_STATUSES),
        )
    )
    await db.commit()
    return rowcount(result)
