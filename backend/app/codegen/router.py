from __future__ import annotations

from fastapi import Depends
from fastapi.responses import Response

from app.codegen.generator import GENERATED_ROOT, CodeGenerator
from app.codegen.schemas import GenerateRequest
from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import success
from app.core.routing import protected_router
from app.db.session import engine

router = protected_router(
    prefix="/tool/codegen",
    tags=["代码生成"],
    dependencies=[Depends(require_admin)],
)
generator = CodeGenerator(engine)


@router.get("/tables", summary="可生成数据表", permission="tool:codegen:list")
async def list_tables(ctx: AuthContext = Depends(get_current_user)):
    return success(await generator.list_tables())


@router.get("/tables/{table}/columns", summary="数据表列信息", permission="tool:codegen:list")
async def table_columns(table: str, ctx: AuthContext = Depends(get_current_user)):
    return success(await generator.table_columns(table))


@router.post("/preview", summary="预览生成代码", permission="tool:codegen:generate")
async def preview(
    data: GenerateRequest,
    ctx: AuthContext = Depends(get_current_user),
):
    files = await generator.generate(
        table=data.table, module=data.module, resource=data.resource, title=data.title
    )
    base = f"{data.module}/{data.resource or data.table}"
    return success(
        {"files": [{"path": f"{base}/{path}", "content": content} for path, content in files.items()]}
    )


@router.post("/download", summary="下载生成代码 zip", permission="tool:codegen:generate")
async def download(
    data: GenerateRequest,
    ctx: AuthContext = Depends(get_current_user),
):
    files = await generator.generate(
        table=data.table, module=data.module, resource=data.resource, title=data.title
    )
    content = generator.zip_bytes(files, prefix=f"{data.module}-{data.resource or data.table}")
    filename = f"{data.module}-{data.resource or data.table}.zip"
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/write", summary="写入 generated 目录", permission="tool:codegen:generate")
async def write(
    data: GenerateRequest,
    ctx: AuthContext = Depends(get_current_user),
):
    files = await generator.generate(
        table=data.table, module=data.module, resource=data.resource, title=data.title
    )
    target = GENERATED_ROOT / data.module / (data.resource or data.table)
    written = generator.write_files(files, target)
    return success({"written": written}, "代码已写入 generated 目录，未覆盖任何已有文件")
