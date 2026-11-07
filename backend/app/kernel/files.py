"""平台级文件存储能力（内核层）。

文件对象 ``FileObject`` 不是某个业务模块的私有数据：头像（auth）、个人资料
（system）、公告附件（content）以及「工具中心 → 文件管理」页面都直接读写它。
把这些能力留在 ``modules/content`` 里，会让「上传头像」这种平台动作被迫依赖
公告业务模块，模块依赖图因此多出两条没有业务含义的边。

本模块承接三件事：

1. 落盘与元数据写入（``upload_file``）——扩展名白名单、magic number 校验、
   同一上传者 sha256 去重都在这里；
2. 读取与鉴权（``get_file`` / ``file_path``）——数据范围判定，避免越权下载；
3. 生命周期（``delete_file`` / ``cleanup_orphan_files``）——删除前清理
   ``User.avatar_file_id`` 悬挂引用。

与 ``modules/content`` 的分工：公告业务留在 content，文件存储属于平台能力。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import api_path, settings
from app.core.errors import AppError
from app.core.pagination import PageParams, count_rows
from app.core.search import LIKE_ESCAPE, contains
from app.core.serializers import iso
from app.core.upload import validate_upload
from app.db.base import utcnow
from app.kernel.authz import AuthContext, get_data_scope, scope_user_ids
from app.kernel.db import commit
from app.kernel.models import FileObject, User

CHUNK_SIZE = 1024 * 1024
STORAGE_ROOT = Path(settings.storage_local_dir).resolve()


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


def _safe_original_name(name: str | None) -> str:
    base = Path(name or "file").name.strip()
    base = re.sub(r"[\x00-\x1f]", "", base)
    return (base or "file")[:255]


def storage_file(item: FileObject) -> Path:
    """把库里的相对路径还原成绝对路径，并确保没有跳出存储根目录。"""
    path = (STORAGE_ROOT / item.storage_path).resolve()
    if not path.is_relative_to(STORAGE_ROOT):
        raise AppError("TOOL_FILE_PATH_INVALID", status_code=400)
    return path


async def upload_file(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    filename: str | None,
    content_type: str | None,
    stream: UploadFile,
    image_only: bool = False,
) -> dict[str, Any]:
    del content_type
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
    except BaseException:
        # 用 BaseException 而不是 Exception：客户端中途断开时 await stream.read()
        # 抛的是 CancelledError（BaseException 子类），只捕 Exception 会把一个
        # 半截文件永久留在存储目录里——它没有 DB 行，孤儿清理永远扫不到。
        target.unlink(missing_ok=True)
        raise
    if size == 0:
        target.unlink(missing_ok=True)
        raise AppError("TOOL_FILE_EMPTY", status_code=422)

    # 落盘之后才校验类型：扩展名白名单 + magic number 一致性都在这一步。
    # 校验失败必须自己删掉刚落盘的临时文件：它在库里没有任何行，
    # cleanup_orphan_files 只扫 DB 记录，永远回收不到，属于纯磁盘泄漏。
    try:
        suffix, detected_type = validate_upload(original_name, header, image_only=image_only)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    sha256 = digest.hexdigest()

    # 同一上传者重复上传同一内容时不另存一份：既省存储，也让「我的文件」
    # 列表不会因为反复上传同一份合同而刷屏。只在同一上传者内去重，
    # 否则返回别人的文件记录等于把数据范围开了一道口子。
    existing = await find_duplicate(db, ctx, sha256)
    if existing is not None:
        target.unlink(missing_ok=True)
        return file_out(existing, uploader_name=ctx.user.username)

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
        await commit(db)
    except BaseException:
        # 提交失败/被取消都要把文件删掉并回滚会话：留着文件是磁盘泄漏，
        # 留着未回滚的会话会让同一请求里后续的查询继续踩在坏事务上。
        final_target.unlink(missing_ok=True)
        await db.rollback()
        raise
    await db.refresh(item)
    return file_out(item, uploader_name=ctx.user.username)


async def find_duplicate(
    db: AsyncSession, ctx: AuthContext, sha256: str
) -> FileObject | None:
    """按 sha256 找同一上传者的既有文件；文件被删时记录会一并删除，所以不会返回悬空行。"""
    statement = (
        select(FileObject)
        .where(FileObject.sha256 == sha256, FileObject.uploader_id == ctx.user.id)
        .order_by(FileObject.id.desc())
        .limit(1)
    )
    return (await db.execute(statement)).scalars().first()


async def list_files(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
    *,
    keyword: str | None = None,
    uploader_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    statement = select(FileObject, User.username).outerjoin(User, FileObject.uploader_id == User.id)
    allowed = await scope_user_ids(db, ctx)
    if allowed is not None:
        statement = statement.where(FileObject.uploader_id.in_(allowed))
    like = contains(keyword)
    if like is not None:
        statement = statement.where(FileObject.original_name.like(like, escape=LIKE_ESCAPE))
    if uploader_id:
        statement = statement.where(FileObject.uploader_id == uploader_id)
    if start:
        statement = statement.where(FileObject.created_at >= start)
    if end:
        statement = statement.where(FileObject.created_at <= end)
    statement = statement.order_by(FileObject.id.desc())
    # 需要额外列（上传者用户名）所以不能用 paginate()，但 count 走同一个入口
    total = await count_rows(db, statement)
    rows = (await db.execute(statement.offset(params.offset).limit(params.page_size))).all()
    return [file_out(item, uploader_name=name) for item, name in rows], total


async def get_file(db: AsyncSession, ctx: AuthContext, file_id: int) -> FileObject:
    item = await db.get(FileObject, file_id)
    if item is None:
        raise AppError("TOOL_FILE_NOT_FOUND", status_code=404)
    if not ctx.is_superuser:
        scope = await get_data_scope(db, ctx)
        if not scope.all_access:
            allowed = item.uploader_id == ctx.user.id
            if not allowed and item.uploader_id:
                uploader = await db.get(User, item.uploader_id)
                allowed = bool(uploader and uploader.dept_id in scope.dept_ids)
            if not allowed:
                raise AppError("TOOL_FILE_FORBIDDEN", status_code=403)
    return item


async def get_file_meta(db: AsyncSession, ctx: AuthContext, file_id: int) -> dict[str, Any]:
    item = await get_file(db, ctx, file_id)
    uploader = await db.get(User, item.uploader_id) if item.uploader_id else None
    return file_out(item, uploader_name=uploader.username if uploader else None)


def file_path(item: FileObject) -> Path:
    path = storage_file(item)
    if not path.exists() or not path.is_file():
        raise AppError("TOOL_FILE_MISSING", status_code=404)
    return path


async def delete_file(db: AsyncSession, ctx: AuthContext, file_id: int) -> None:
    item = await get_file(db, ctx, file_id)
    path = storage_file(item)
    # 删除文件前先清掉把它当头像的用户引用，避免 User.avatar_file_id 悬挂。
    await db.execute(
        update(User).where(User.avatar_file_id == file_id).values(avatar_file_id=None)
    )
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
        path = storage_file(item)
        # 防御性清理：删除前把可能指向该文件的头像引用一并置空（引用完整性）。
        await db.execute(
            update(User).where(User.avatar_file_id == item.id).values(avatar_file_id=None)
        )
        await db.delete(item)
        if path.exists():
            await asyncio.to_thread(path.unlink, True)
        count += 1
    await db.commit()
    return count
