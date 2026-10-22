from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import filetype
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_data_scope, scope_user_ids
from app.core.config import api_path, settings
from app.core.errors import AppError
from app.core.pagination import PageParams, paginate
from app.core.serializers import iso
from app.core.upload import validate_upload
from app.db.base import utcnow
from app.modules.content.models import FileObject, Notice, NoticeRead
from app.modules.content.schemas import NoticeCreate, NoticeUpdate
from app.modules.system.models import User

CHUNK_SIZE = 1024 * 1024
STORAGE_ROOT = Path(settings.storage_local_dir).resolve()

# 可在浏览器内联展示的图片类型白名单。SVG/HTML 等文本格式会被排除：
# 它们与后台同源，内联返回等于把存储型 XSS 直接送进管理页面。
SAFE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"})


def detect_image_type(header: bytes) -> str | None:
    """只信任服务端嗅探结果，识别失败或不在白名单都返回 ``None``。"""
    if not header:
        return None
    guessed = filetype.guess(header)
    mime = guessed.mime if guessed else None
    return mime if mime in SAFE_IMAGE_TYPES else None


def is_safe_image(item: FileObject) -> bool:
    return (item.content_type or "") in SAFE_IMAGE_TYPES


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


def file_out(item: FileObject, *, uploader_name: str | None = None) -> dict[str, Any]:
    return {
        "id": item.id,
        "original_name": item.original_name,
        "content_type": item.content_type,
        "size": item.size,
        "sha256": item.sha256,
        "uploader_id": item.uploader_id,
        "uploader_name": uploader_name,
        "created_at": iso(item.created_at),
        "download_url": api_path(f"/tool/files/{item.id}/download"),
    }


async def list_notices(
    db: AsyncSession,
    params: PageParams,
    *,
    keyword: str | None = None,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    statement = select(Notice)
    if keyword:
        like = f"%{keyword.strip()}%"
        statement = statement.where(or_(Notice.title.like(like), Notice.content.like(like)))
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
    from app.modules.notify import hooks

    await hooks.notice_published(
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
    await db.commit()
    await db.refresh(item)
    if data.status == "published":
        # 直接建为已发布的公告同样要推送，否则触发点会被绕过。
        await _emit_published(item, ctx.user.username)
    return notice_out(item)


async def update_notice(db: AsyncSession, ctx: AuthContext, notice_id: int, data: NoticeUpdate) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    item.updated_by = ctx.user.id
    await db.commit()
    return notice_out(item)


async def publish_notice(db: AsyncSession, ctx: AuthContext, notice_id: int) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    was_published = item.status == "published"
    item.status = "published"
    item.published_at = item.published_at or utcnow()
    item.updated_by = ctx.user.id
    await db.commit()
    # 重复点发布/请求重试不应该再推一次，只有状态真的从草稿或下线变为已发布才推送
    if not was_published:
        await _emit_published(item, ctx.user.username)
    return notice_out(item)


async def unpublish_notice(db: AsyncSession, ctx: AuthContext, notice_id: int) -> dict[str, Any]:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    if item.status != "published":
        raise AppError("CONTENT_NOTICE_NOT_PUBLISHED", "公告未处于已发布状态", 400)
    item.status = "offline"
    item.updated_by = ctx.user.id
    await db.commit()
    return notice_out(item)


async def delete_notice(db: AsyncSession, notice_id: int) -> None:
    item = await db.get(Notice, notice_id)
    if item is None:
        raise AppError("CONTENT_NOTICE_NOT_FOUND", "公告不存在", 404)
    await db.delete(item)
    await db.commit()


async def list_published_notices(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
) -> tuple[list[dict[str, Any]], int]:
    read_exists = exists(
        select(NoticeRead.id).where(
            NoticeRead.notice_id == Notice.id,
            NoticeRead.user_id == ctx.user.id,
        )
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
        await db.commit()


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
    await db.commit()
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


def _safe_original_name(name: str | None) -> str:
    base = Path(name or "file").name.strip()
    base = re.sub(r"[\x00-\x1f]", "", base)
    return (base or "file")[:255]


def _storage_file(item: FileObject) -> Path:
    path = (STORAGE_ROOT / item.storage_path).resolve()
    if not path.is_relative_to(STORAGE_ROOT):
        raise AppError("TOOL_FILE_PATH_INVALID", "文件路径非法", 400)
    return path


async def upload_file(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    filename: str | None,
    content_type: str | None,
    stream,  # type: ignore[no-untyped-def]
    image_only: bool = False,
) -> dict[str, Any]:
    original_name = _safe_original_name(filename)
    now = utcnow()
    relative_dir = Path(f"{now.year:04d}") / f"{now.month:02d}"
    absolute_dir = STORAGE_ROOT / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}"
    target = absolute_dir / stored_name
    max_bytes = settings.upload_max_mb * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    header = b""
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                if not header:
                    header = chunk[:8192]
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(
                        "TOOL_FILE_TOO_LARGE",
                        f"文件超过 {settings.upload_max_mb} MB 限制",
                        413,
                    )
                digest.update(chunk)
                await asyncio.to_thread(handle.write, chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if size == 0:
        target.unlink(missing_ok=True)
        raise AppError("TOOL_FILE_EMPTY", "不能上传空文件", 422)

    # 落盘之后才校验类型：扩展名白名单 + magic number 一致性都在这一步，
    # 通不过就把临时文件删掉，不留半成品在存储目录里。
    suffix, detected_type = validate_upload(original_name, header, image_only=image_only)
    sha256 = digest.hexdigest()

    # 同一上传者重复上传同一内容时不另存一份：既省存储，也让「我的文件」
    # 列表不会因为反复上传同一份合同而刷屏。只在同一上传者内去重，
    # 否则返回别人的文件记录等于把数据范围开了一道口子。
    existing = await _find_duplicate(db, ctx, sha256)
    if existing is not None:
        target.unlink(missing_ok=True)
        uploader_name = ctx.user.username
        return file_out(existing, uploader_name=uploader_name)

    final_name = f"{stored_name}{suffix}"
    final_target = absolute_dir / final_name
    await asyncio.to_thread(target.rename, final_target)
    item = FileObject(
        original_name=original_name,
        stored_name=final_name,
        storage_path=str(relative_dir / final_name).replace("\\", "/"),
        content_type=detected_type,
        size=size,
        sha256=sha256,
        uploader_id=ctx.user.id,
    )
    db.add(item)
    try:
        await db.commit()
    except Exception:
        final_target.unlink(missing_ok=True)
        raise
    await db.refresh(item)
    return file_out(item, uploader_name=ctx.user.username)


async def _find_duplicate(db: AsyncSession, ctx: AuthContext, sha256: str):  # type: ignore[no-untyped-def]
    """按 sha256 找同一上传者的既有文件；文件被删时记录会一并删除，所以不会返回悬空行。"""
    statement = (
        select(FileObject)
        .where(FileObject.sha256 == sha256, FileObject.uploader_id == ctx.user.id)
        .order_by(FileObject.id.desc())
        .limit(1)
    )
    return (await db.execute(statement)).scalars().first()


async def _allowed_uploader_query(db: AsyncSession, ctx: AuthContext):  # type: ignore[no-untyped-def]
    return await scope_user_ids(db, ctx)


async def list_files(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
    *,
    keyword: str | None = None,
    uploader_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    statement = select(FileObject, User.username).outerjoin(User, FileObject.uploader_id == User.id)
    allowed = await _allowed_uploader_query(db, ctx)
    if allowed is not None:
        statement = statement.where(FileObject.uploader_id.in_(allowed))
    if keyword:
        statement = statement.where(FileObject.original_name.like(f"%{keyword.strip()}%"))
    if uploader_id:
        statement = statement.where(FileObject.uploader_id == uploader_id)
    if start:
        statement = statement.where(FileObject.created_at >= start)
    if end:
        statement = statement.where(FileObject.created_at <= end)
    statement = statement.order_by(FileObject.id.desc())
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int((await db.execute(count_statement)).scalar() or 0)
    rows = (
        (await db.execute(statement.offset(params.offset).limit(params.page_size))).all()
    )
    return [file_out(item, uploader_name=name) for item, name in rows], total


async def get_file(db: AsyncSession, ctx: AuthContext, file_id: int) -> FileObject:
    item = await db.get(FileObject, file_id)
    if item is None:
        raise AppError("TOOL_FILE_NOT_FOUND", "文件不存在", 404)
    if not ctx.is_superuser:
        scope = await get_data_scope(db, ctx)
        if not scope.all_access:
            allowed = item.uploader_id == ctx.user.id
            if not allowed and item.uploader_id:
                uploader = await db.get(User, item.uploader_id)
                allowed = bool(uploader and uploader.dept_id in scope.dept_ids)
            if not allowed:
                raise AppError("TOOL_FILE_FORBIDDEN", "无权访问该文件", 403)
    return item


async def get_file_meta(db: AsyncSession, ctx: AuthContext, file_id: int) -> dict[str, Any]:
    item = await get_file(db, ctx, file_id)
    uploader = await db.get(User, item.uploader_id) if item.uploader_id else None
    return file_out(item, uploader_name=uploader.username if uploader else None)


def file_path(item: FileObject) -> Path:
    path = _storage_file(item)
    if not path.exists() or not path.is_file():
        raise AppError("TOOL_FILE_MISSING", "文件内容已丢失", 404)
    return path


async def delete_file(db: AsyncSession, ctx: AuthContext, file_id: int) -> None:
    item = await get_file(db, ctx, file_id)
    path = _storage_file(item)
    await db.delete(item)
    await db.commit()
    if path.exists():
        await asyncio.to_thread(path.unlink, True)


async def cleanup_orphan_files(db: AsyncSession, *, days: int = 30) -> int:
    cutoff = utcnow() - timedelta(days=max(days, 1))
    referenced = select(User.avatar_file_id).where(User.avatar_file_id.is_not(None))
    items = (
        (
            await db.execute(
                select(FileObject).where(
                    FileObject.created_at < cutoff, FileObject.id.not_in(referenced)
                )
            )
        )
        .scalars()
        .all()
    )
    count = 0
    for item in items:
        path = _storage_file(item)
        await db.delete(item)
        if path.exists():
            await asyncio.to_thread(path.unlink, True)
        count += 1
    await db.commit()
    return count


# 说明：不提供「清理已读记录」的定时任务。NoticeRead 是未读计数的唯一依据，
# 删除旧记录会让用户重新看到「未读」，属于错误行为，因此这里没有对应的孤儿逻辑。
