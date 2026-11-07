"""探活与元信息端点（A-6：从 ``main.py`` 拆出）。

原来这三个端点与异常处理器、中间件、静态挂载一起挤在 ``main.py``。
拆出后 ``main.py`` 只保留 ``create_app()`` 与 ``lifespan``，装配职责分别落到
``app.assembly``（中间件 / 路由 / 静态）与本模块（健康检查 / 元信息）。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

from app.core.cache import get_cache
from app.core.config import settings
from app.core.envelope import Envelope, StrictModel
from app.core.errors import get_request_id, success
from app.core.tz import current_timezone
from app.db.migrate import head_revision
from app.db.session import engine

logger = logging.getLogger("fastcms")


class HealthOut(StrictModel):
    status: str
    version: str


class ReadyOut(StrictModel):
    """就绪响应：数据库与迁移版本都要对上；不一致时回 503 并带上期望值。"""

    status: str
    database: str
    migration: str | None = None
    expected: str | None = None
    redis: str | None = None


class MetaOut(StrictModel):
    name: str
    version: str
    timezone: str
    server_time: str


def register_health_routes(app: FastAPI) -> None:
    """注册存活 / 就绪 / 元信息端点（及可选的指标端点）。"""

    @app.get("/healthz", tags=["健康检查"], summary="存活检查", response_model=HealthOut)
    async def healthz():
        return {"status": "ok", "version": settings.app_version}

    @app.get(
        "/readyz",
        tags=["健康检查"],
        summary="就绪检查",
        response_model=ReadyOut,
        responses={503: {"model": ReadyOut, "description": "依赖不可用或迁移未到位"}},
    )
    async def readyz():
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                has_version = await connection.run_sync(
                    lambda sync: inspect(sync).has_table("alembic_version")
                )
                current = None
                if has_version:
                    current = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        except Exception as exc:
            # 驱动异常常带连接串信息，不能原样回给匿名调用方
            logger.exception("readz database unavailable request_id=%s", get_request_id(), exc_info=exc)
            return JSONResponse(
                status_code=503,
                content={"status": "error", "database": "unavailable"},
            )
        head = head_revision()
        if current != head:
            return JSONResponse(
                status_code=503,
                content={"status": "error", "database": "ok", "migration": current, "expected": head},
            )
        if settings.redis_enabled:
            # 启动时 ping 过一次，运行期 Redis 仍可能断开，就绪判定必须复核
            try:
                await get_cache().ping()
            except Exception as exc:
                logger.exception("readz redis unavailable request_id=%s", get_request_id(), exc_info=exc)
                return JSONResponse(
                    status_code=503,
                    content={"status": "error", "database": "ok", "redis": "unavailable"},
                )
        return {"status": "ok", "database": "ok", "migration": current}

    @app.get(
        f"{settings.api_prefix}/meta/version",
        tags=["元信息"],
        summary="应用版本与服务器时间",
        response_model=Envelope[MetaOut],
    )
    async def meta_version():
        from app.db.base import utcnow

        # 匿名可访问，只暴露品牌与时间；Python/数据库/调度模式等环境指纹走需要权限的 /monitor/server
        return success(
            {
                "name": settings.app_name,
                "version": settings.app_version,
                "timezone": current_timezone(),
                "server_time": utcnow().isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
        )

    if settings.metrics_enabled:
        # B-8 / 4-2：Prometheus 文本格式，供抓取器采集 QPS / 延迟 / 连接池视角
        @app.get(
            "/metrics",
            tags=["监控"],
            summary="Prometheus 指标（可选，METRICS_ENABLED=true）",
            responses={
                200: {
                    "description": "Prometheus 文本格式",
                    "content": {"text/plain": {"schema": {"type": "string"}}},
                }
            },
        )
        async def metrics():
            from fastapi.responses import PlainTextResponse
            from sqlalchemy import func, select

            from app.core import metrics as metrics_mod
            from app.db.session import async_session_factory
            from app.kernel.models import EventOutbox

            # outbox 积压需要查库（Phase 11-4）：取值是 async 的，没法注册成
            # metrics 模块的同步 gauge 回调，所以由本端点（async）先查再传入。
            # 查询失败按缺失处理，不能让整个抓取 500。
            extra: dict[str, float] = {}
            try:
                async with async_session_factory() as session:
                    pending = (
                        await session.execute(
                            select(func.count())
                            .select_from(EventOutbox)
                            .where(EventOutbox.status == "pending")
                        )
                    ).scalar_one()
                extra["fastcms_events_outbox_backlog"] = float(pending)
            except Exception:
                # 积压量恰恰在数据库不健康时最该被看到；DEBUG 会让这个 gauge
                # 在最需要它的时刻静默消失。
                logger.warning("outbox 积压统计失败，本轮 gauge 缺失", exc_info=True)

            return PlainTextResponse(
                metrics_mod.render(extra),
                media_type="text/plain; version=0.0.4; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )
