from __future__ import annotations

import asyncio

from fastapi import Depends
from fastapi.responses import Response

from app.codegen.generator import GENERATED_ROOT, CodeGenerator
from app.codegen.schemas import (
    CodegenColumnOut,
    CodegenPreviewOut,
    CodegenTableOut,
    CodegenWriteOut,
    GenerateRequest,
)
from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.envelope import Envelope
from app.core.errors import success
from app.core.routing import protected_router
from app.db.session import engine

router = protected_router(
    prefix="/tool/codegen",
    tags=["代码生成"],
    dependencies=[Depends(require_admin)],
)
generator = CodeGenerator(engine)


@router.get(
    "/tables",
    summary="可生成数据表",
    permission="tool:codegen:list",
    response_model=Envelope[list[CodegenTableOut]],
)
async def list_tables(_ctx: AuthContext = Depends(get_current_user)):
    return success(await generator.list_tables())


@router.get(
    "/tables/{table}/columns",
    summary="数据表列信息",
    permission="tool:codegen:list",
    response_model=Envelope[list[CodegenColumnOut]],
)
async def table_columns(table: str, _ctx: AuthContext = Depends(get_current_user)):
    return success(await generator.table_columns(table))


@router.post(
    "/preview",
    summary="预览生成代码",
    permission="tool:codegen:generate",
    response_model=Envelope[CodegenPreviewOut],
)
async def preview(
    data: GenerateRequest,
    _ctx: AuthContext = Depends(get_current_user),
):
    files = await generator.generate(
        table=data.table, module=data.module, resource=data.resource, title=data.title, domain=data.domain
    )
    base = f"{data.module}/{data.resource or data.table}"
    return success(
        {"files": [{"path": f"{base}/{path}", "content": content} for path, content in files.items()]}
    )


@router.post(
    "/download",
    summary="下载生成代码 zip",
    permission="tool:codegen:generate",
    responses={
        200: {
            "description": "生成的代码 zip 包",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def download(
    data: GenerateRequest,
    _ctx: AuthContext = Depends(get_current_user),
):
    files = await generator.generate(
        table=data.table, module=data.module, resource=data.resource, title=data.title, domain=data.domain
    )
    # zip 压缩与落盘都是同步 CPU / 文件 IO，直接放进事件循环会阻塞所有并发请求。
    # 与 core/security.py、kernel/files.py 同口径：卸载到线程池。
    content = await asyncio.to_thread(
        generator.zip_bytes, files, prefix=f"{data.module}-{data.resource or data.table}"
    )
    filename = f"{data.module}-{data.resource or data.table}.zip"
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/write",
    summary="写入 generated 目录",
    permission="tool:codegen:generate",
    response_model=Envelope[CodegenWriteOut],
)
async def write(
    data: GenerateRequest,
    _ctx: AuthContext = Depends(get_current_user),
):
    files = await generator.generate(
        table=data.table, module=data.module, resource=data.resource, title=data.title, domain=data.domain
    )
    target = GENERATED_ROOT / data.module / (data.resource or data.table)
    written = await asyncio.to_thread(generator.write_files, files, target)
    return success({"written": written}, "代码已写入 generated 目录，未覆盖任何已有文件")
