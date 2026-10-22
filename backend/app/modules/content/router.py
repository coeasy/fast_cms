from __future__ import annotations

from datetime import datetime

from fastapi import Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import success
from app.core.pagination import PageParams, page_data
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.content import service
from app.modules.content.schemas import NoticeCreate, NoticeUpdate

router = protected_router(tags=["内容管理"])
admin_router = protected_router(
    tags=["内容管理"],
    dependencies=[Depends(require_admin)],
)


@router.get("/content/notices/published", summary="已发布公告（用户端）")
async def list_published_notices(
    params: PageParams = Depends(),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_published_notices(db, ctx, params)
    return success(page_data(items, total, params))


@router.get("/content/notices/unread-count", summary="未读公告数量")
async def unread_notice_count(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success({"count": await service.unread_notice_count(db, ctx)})


@router.post("/content/notices/read-all", summary="全部公告标记已读")
async def read_all_notices(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count = await service.mark_all_notices_read(db, ctx)
    return success({"count": count}, "已全部标记为已读")


@router.get("/content/notices/{notice_id}/published", summary="公告详情（用户端）")
async def get_published_notice(
    notice_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_published_notice(db, ctx, notice_id))


@router.post("/content/notices/{notice_id}/read", summary="标记公告已读")
async def mark_notice_read(
    notice_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.mark_notice_read(db, ctx, notice_id)
    return success(None, "已标记为已读")


@admin_router.get("/content/notices", summary="公告列表（管理端）", permission="system:notice:list")
async def list_notices(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, pattern="^(draft|published)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_notices(db, params, keyword=keyword, status=status)
    return success(page_data(items, total, params))


@admin_router.post("/content/notices", summary="创建公告", permission="system:notice:create")
async def create_notice(
    data: NoticeCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_notice(db, ctx, data), "公告已创建")


@admin_router.get("/content/notices/{notice_id}", summary="公告详情（管理端）", permission="system:notice:list")
async def get_notice(
    notice_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_notice(db, notice_id))


@admin_router.put("/content/notices/{notice_id}", summary="更新公告", permission="system:notice:update")
async def update_notice(
    notice_id: int,
    data: NoticeUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_notice(db, ctx, notice_id, data), "公告已更新")


@admin_router.post("/content/notices/{notice_id}/publish", summary="发布公告", permission="system:notice:publish")
async def publish_notice(
    notice_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.publish_notice(db, ctx, notice_id), "公告已发布")


@admin_router.post("/content/notices/{notice_id}/unpublish", summary="下线公告", permission="system:notice:publish")
async def unpublish_notice(
    notice_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.unpublish_notice(db, ctx, notice_id), "公告已下线")


@admin_router.delete("/content/notices/{notice_id}", summary="删除公告", permission="system:notice:delete")
async def delete_notice(
    notice_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_notice(db, notice_id)
    return success(None, "公告已删除")


@admin_router.get("/tool/files", summary="文件列表", permission="tool:file:list")
async def list_files(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=255),
    uploader_id: int | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_files(
        db, ctx, params, keyword=keyword, uploader_id=uploader_id, start=start, end=end
    )
    return success(page_data(items, total, params))


@admin_router.post("/tool/files/upload", summary="上传文件", permission="tool:file:upload")
async def upload_file(
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(
        await service.upload_file(
            db,
            ctx,
            filename=file.filename,
            content_type=file.content_type,
            stream=file,
        ),
        "上传成功",
    )


@admin_router.get("/tool/files/{file_id}", summary="文件元数据", permission="tool:file:list")
async def get_file(
    file_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_file_meta(db, ctx, file_id))


@admin_router.get("/tool/files/{file_id}/download", summary="下载文件", permission="tool:file:download")
async def download_file(
    file_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = await service.get_file(db, ctx, file_id)
    path = service.file_path(item)
    return FileResponse(
        path,
        media_type=item.content_type or "application/octet-stream",
        filename=item.original_name,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_quote_filename(item.original_name)}",
            # 没有 nosniff，浏览器会忽略上面的 media_type 自己猜：
            # 一个 text/plain 的 HTML 会被当页面渲染，存储型 XSS 就成了。
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@admin_router.delete("/tool/files/{file_id}", summary="删除文件", permission="tool:file:delete")
async def delete_file(
    file_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_file(db, ctx, file_id)
    return success(None, "文件已删除")


def _quote_filename(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")
