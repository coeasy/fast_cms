from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.envelope import Envelope
from app.core.errors import success
from app.core.routing import protected_router
from app.db.session import get_db
from app.kernel.schemas import OnlineSessionOut
from app.modules.ops.monitor import service
from app.modules.ops.monitor.schemas import ServerInfoOut
from app.modules.sys.system.service import kick_session

router = protected_router(
    prefix="/monitor",
    tags=["系统监控"],
    dependencies=[Depends(require_admin)],
)


@router.get("/online", summary="在线用户", permission="monitor:online:list", response_model=Envelope[list[OnlineSessionOut]])
async def list_online(
    keyword: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_online(db, ctx, keyword=keyword))


@router.delete("/online/{session_id}", summary="强制下线", permission="monitor:online:kick", response_model=Envelope[None])
async def kick_online_session(
    session_id: str,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await kick_session(db, ctx, session_id)
    return success(None, "会话已强制下线")


@router.get("/server", summary="服务器信息", permission="monitor:server:read", response_model=Envelope[ServerInfoOut])
async def server_info(_ctx: AuthContext = Depends(get_current_user)):
    # psutil 调用是阻塞的（cpu_percent 还要睡 100ms），必须走线程池
    return success(await service.server_info_async())
