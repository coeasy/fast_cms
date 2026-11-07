from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.assembly import (
    STATIC_DIR,
    include_module_routers,
    mount_web,
    register_exception_handlers,
    register_middleware,
)
from app.core.config import settings
from app.db.session import engine
from app.health import register_health_routes
from app.startup import app_lifespan

logger = logging.getLogger("fastcms")

__all__ = ["STATIC_DIR", "app", "create_app", "engine"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with app_lifespan(_app):
        yield


def create_app() -> FastAPI:
    """装配根：仅负责建 app、挂处理器/中间件/路由/静态，启动引导交给 ``lifespan``。"""
    api_prefix = settings.api_prefix
    docs_enabled = settings.app_docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    register_exception_handlers(app)
    register_middleware(app)
    include_module_routers(app, api_prefix)
    register_health_routes(app)
    mount_web(app)

    return app


app = create_app()
