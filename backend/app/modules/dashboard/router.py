from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import success
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.dashboard import service

router = protected_router(
    prefix="/dashboard",
    tags=["仪表盘"],
    dependencies=[Depends(require_admin)],
)


@router.get("/summary", summary="仪表盘汇总", permission="dashboard:view")
async def summary(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.summary(db))
