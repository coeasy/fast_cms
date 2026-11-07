"""公告业务（content 模块）。

文件存储能力已迁至 ``app/kernel/files.py``：上传、下载、删除、孤儿清理都是
平台能力（头像 / 个人资料 / 文件管理页面共用），不属于公告业务。
本文件只保留公告的增删改查、发布下线与已读状态。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.authz import AuthContext
from app.core.errors import AppError
from app.core.pagination import PageParams, paginate
from app.core.search import LIKE_ESCAPE, contains
from app.core.serializers import iso
from app.db.base import utcnow
from app.kernel.db import after_commit, commit
from app.modules.content.models import Notice, NoticeRead
from app.modules.content.schemas import NoticeCreate, NoticeUpdate


def notice_out(item: Notice, *, read: bool | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id,
        "title": item.title,
        "content": item.content,
        "status": item.status,
        "is_top": item.is_top,
        "published_at": iso(item.published_at),
        "created_by": item.created_by,
        "updated_by": item.updated_by,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
    if read is not None:
        data["read"] = read
    return data


async def list_notices(
    db: AsyncSession,
    params: PageParams,
    *,
    keyword: str | None = None,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    statement = select(Notice)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                Notice.title.like(like, escape=LIKE_ESCAPE),
                Notice.content.like(like, escape=LIKE_ESCAPE),
            )
        )
    if status:
        statement = statement.where(Notice.status == status)
    statement = statement.order_by(Notice.is_top.desc(), Notice.id.desc())
    rows, total = await paginate(db, statement, params)
    return [notice_out(item) for item in rows], total


async def get_notice(db: AsyncSession, notice_id: int) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    return notice_out(item)


async def _emit_published(item: Notice, publisher: str) -> None:
    # 只声明「公告已发布」，谁订阅（通知推送、Webhook）由订阅方自己决定。
    # 直接 import notify 会让 content 反向依赖通知模块（见 app/core/events.py）。
    await events.publish(
        "notice.published",
        notice_id=int(item.id),
        title=item.title,
        content=item.content,
        published_at=item.published_at,
        publisher=publisher,
    )


async def create_notice(db: AsyncSession, ctx: AuthContext, data: NoticeCreate) -> dict[str, Any]:
    now = utcnow()
    item = Notice(
        title=data.title,
        content=data.content,
        status=data.status,
        is_top=data.is_top,
        published_at=now if data.status == "published" else None,
        created_by=ctx.user.id,
        updated_by=ctx.user.id,
    )
    db.add(item)
    if data.status == "published":
        # 直接建为已发布的公告同样要推送，否则触发点会被绕过。
        # 挂到 after_commit：事务没提交成功就不该有推送，否则会出现
        # 「收到通知但点进去 404」——排查成本最高的一类不一致。
        after_commit(lambda: _emit_published(item, ctx.user.username))
    await commit(db)
    await db.refresh(item)
    return notice_out(item)


async def update_notice(db: AsyncSession, ctx: AuthContext, notice_id: int, data: NoticeUpdate) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    item.updated_by = ctx.user.id
    await commit(db)
    return notice_out(item)


async def publish_notice(db: AsyncSession, ctx: AuthContext, notice_id: int) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    was_published = item.status == "published"
    item.status = "published"
    item.published_at = item.published_at or utcnow()
    item.updated_by = ctx.user.id
    # 重复点发布/请求重试不应该再推一次，只有状态真的从草稿或下线变为已发布才推送
    if not was_published:
        after_commit(lambda: _emit_published(item, ctx.user.username))
    await commit(db)
    return notice_out(item)


async def unpublish_notice(db: AsyncSession, ctx: AuthContext, notice_id: int) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    if item.status != "published":
        raise AppError("CONTENT_NOTICE_NOT_PUBLISHED", status_code=400)
    item.status = "offline"
    item.updated_by = ctx.user.id
    await commit(db)
    return notice_out(item)


async def delete_notice(db: AsyncSession, notice_id: int) -> None:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    await db.delete(item)
    await commit(db)


async def list_published_notices(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
) -> tuple[list[dict[str, Any]], int | None]:
    # 匿名（前台）没有已读状态，直接给 FALSE 字面量；登录用户才关联 NoticeRead
    read_exists = (
        exists(
            select(NoticeRead.id).where(
                NoticeRead.notice_id == Notice.id,
                NoticeRead.user_id == ctx.user.id,
            )
        )
        if ctx.user.id is not None
        else false()
    )
    statement = (
        select(Notice, read_exists.label("is_read"))
        .where(Notice.status == "published")
        .order_by(Notice.is_top.desc(), Notice.published_at.desc(), Notice.id.desc())
    )
    count_statement = select(func.count(Notice.id)).where(Notice.status == "published")
    total = int((await db.execute(count_statement)).scalar() or 0)
    rows = (
        (await db.execute(statement.offset(params.offset).limit(params.page_size))).all()
    )
    return [notice_out(item, read=bool(is_read)) for item, is_read in rows], total


async def get_published_notice(db: AsyncSession, ctx: AuthContext, notice_id: int) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None or item.status != "published":
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在或未发布", 404)
    if ctx.user.id is None:
        return notice_out(item, read=False)
    read = (
        await db.execute(
            select(NoticeRead.id).where(
                NoticeRead.notice_id == notice_id, NoticeRead.user_id == ctx.user.id
            )
        )
    ).scalar_one_or_none()
    return notice_out(item, read=read is not None)


async def mark_notice_read(db: AsyncSession, ctx: AuthContext, notice_id: int) -> None:
    item = await db.get(Notice, notice_id)
    if item is None or item.status != "published":
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在或未发布", 404)
    existing = (
        await db.execute(
            select(NoticeRead).where(
                NoticeRead.notice_id == notice_id, NoticeRead.user_id == ctx.user.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(NoticeRead(notice_id=notice_id, user_id=ctx.user.id))
        await commit(db)


async def mark_all_notices_read(db: AsyncSession, ctx: AuthContext) -> int:
    read_ids = select(NoticeRead.notice_id).where(NoticeRead.user_id == ctx.user.id)
    notices = (
        (
            await db.execute(
                select(Notice.id).where(Notice.status == "published", Notice.id.not_in(read_ids))
            )
        )
        .scalars()
        .all()
    )
    for notice_id in notices:
        db.add(NoticeRead(notice_id=notice_id, user_id=ctx.user.id))
    await commit(db)
    return len(notices)


async def unread_notice_count(db: AsyncSession, ctx: AuthContext) -> int:
    read_ids = select(NoticeRead.notice_id).where(NoticeRead.user_id == ctx.user.id)
    return int(
        (
            await db.execute(
                select(func.count(Notice.id)).where(
                    Notice.status == "published", Notice.id.not_in(read_ids)
                )
            )
        ).scalar()
        or 0
    )


# 过渡期兼容导出：文件能力已迁到 app/kernel/files.py，
# 新代码请直接从内核层引用，这里只保证旧引用（如定时任务、测试）不中断。
from app.kernel.files import (  # noqa: E402,F401
    cleanup_orphan_files,
    delete_file,
    file_out,
    file_path,
    get_file,
    get_file_meta,
    list_files,
    storage_file,
    upload_file,
)
