from __future__ import annotations

import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.bootstrap.models import BootstrapVersion
from app.bootstrap.seed import SEED_VERSION, seed_database, sync_permission_codes
from app.codegen.router import router as codegen_router
from app.core import ipgeo
from app.core.boot_lock import BootLock
from app.core.cache import close_cache, get_cache, init_cache
from app.core.config import DATA_DIR, DEFAULT_ADMIN_USERNAME, settings
from app.core.errors import (
    AppError,
    app_error_handler,
    error_payload,
    get_request_id,
    http_error_handler,
    success,
    validation_error_handler,
)
from app.core.ipguard import IpGuardMiddleware
from app.core.logging import configure_logging
from app.core.middleware import OperationLogMiddleware, RequestContextMiddleware
from app.core.ratelimit import RateLimitMiddleware
from app.core.tz import current_timezone
from app.db.migrate import ensure_database, head_revision
from app.db.session import engine, session_scope
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.config.router import public_router as config_public_router
from app.modules.config.router import router as config_router
from app.modules.config.service import load_timezone_from_db
from app.modules.content.router import admin_router as content_admin_router
from app.modules.content.router import router as content_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.export.router import router as export_router
from app.modules.monitor.router import router as monitor_router
from app.modules.notify.router import router as notify_router
from app.modules.schedule.router import router as schedule_router
from app.modules.schedule.scheduler import scheduler_service
from app.modules.security.router import router as security_router
from app.modules.system.router import router as system_router
from app.modules.theme.router import public_router as theme_public_router
from app.modules.theme.router import router as theme_router
from app.web import WEB_DIR, base_path, build_admin_router, build_portal_router, router_prefix

logger = logging.getLogger("fastcms")

# 服务端渲染的 HTML 前端资源目录（CSS / 原生 JS），随后端一起发布
STATIC_DIR = WEB_DIR / "static"

# Windows registry may map .js to text/plain, which makes browsers reject the SPA bundle.
for extension, content_type in (
    (".js", "text/javascript"),
    (".mjs", "text/javascript"),
    (".css", "text/css"),
    (".svg", "image/svg+xml"),
    (".woff2", "font/woff2"),
):
    mimetypes.add_type(content_type, extension)


def _print_initial_admin_notice() -> None:
    banner = "=" * 68
    print(banner)
    print("  已创建初始管理员账号")
    print(f"  用户名：{DEFAULT_ADMIN_USERNAME}")
    if settings.is_production:
        print("  初始密码：取自 INITIAL_ADMIN_PASSWORD，首次登录后必须修改")
    else:
        print(f"  初始密码：{settings.admin_password}（可用 INITIAL_ADMIN_PASSWORD 覆盖）")
    print(banner)


async def _ensure_seed() -> None:
    async with session_scope() as db:
        version = await db.get(BootstrapVersion, "seed")
        if version is not None and version.version == SEED_VERSION:
            # 权限码是代码里的真相，旧库必须补齐新增项，否则升级后接口直接 403
            await sync_permission_codes(db)
            return
        created = await seed_database(db)
    if created:
        _print_initial_admin_notice()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.scheduler_mode == "embedded" and settings.app_workers > 1:
        raise RuntimeError("APP_WORKERS>1 时禁止嵌入式调度器，请设置 SCHEDULER_MODE=standalone 或 disabled")
    configure_logging()
    await init_cache()
    # IP 归属地库提前加载并校验：xdb 缺失只告警，不阻断启动
    await ipgeo.startup()
    # 多 worker 首启时并发执行迁移/种子会让 SQLite 直接报 locked，用文件锁串行化引导过程
    boot_lock = BootLock(DATA_DIR / "bootstrap.lock")
    if not await asyncio.to_thread(boot_lock.acquire):
        raise RuntimeError("等待启动锁超时，请确认是否有其他进程占用 data/bootstrap.lock")
    try:
        await ensure_database()
        await _ensure_seed()
        # 系统时区以数据库参数为准（环境变量只是初值），必须在调度器启动前加载
        async with session_scope() as db:
            system_timezone = await load_timezone_from_db(db)
    finally:
        boot_lock.release()
    if settings.scheduler_mode == "embedded":
        await scheduler_service.start()
    logger.info(
        "application started env=%s version=%s timezone=%s",
        settings.app_env,
        settings.app_version,
        system_timezone,
    )
    try:
        yield
    finally:
        await scheduler_service.shutdown()
        await close_cache()
        await engine.dispose()


def create_app() -> FastAPI:
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

    # Starlette 的处理程序签名声明收 Exception，但运行时按注册的异常类传入 AppError
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )
    # 中间件按「后注册先执行」的顺序包住应用：IP 守卫必须最外层，
    # 被封禁的请求不该再进入限流、审计与业务逻辑。
    app.add_middleware(OperationLogMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(IpGuardMiddleware)
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(Exception)
    async def log_unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error request_id=%s", get_request_id(), exc_info=exc)
        return JSONResponse(status_code=500, content=error_payload("INTERNAL_ERROR", "服务器内部错误，请使用 request_id 联系管理员"))

    @app.get("/healthz", tags=["健康检查"], summary="存活检查")
    async def healthz():
        return {"status": "ok", "version": settings.app_version}

    @app.get("/readyz", tags=["健康检查"], summary="就绪检查")
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
        except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
                logger.exception("readz redis unavailable request_id=%s", get_request_id(), exc_info=exc)
                return JSONResponse(
                    status_code=503,
                    content={"status": "error", "database": "ok", "redis": "unavailable"},
                )
        return {"status": "ok", "database": "ok", "migration": current}

    @app.get(f"{api_prefix}/meta/version", tags=["元信息"], summary="应用版本与服务器时间")
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

    for router in (
        auth_router,
        theme_public_router,
        theme_router,
        system_router,
        config_public_router,
        config_router,
        audit_router,
        monitor_router,
        dashboard_router,
        export_router,
        schedule_router,
        security_router,
        content_router,
        content_admin_router,
        notify_router,
        codegen_router,
    ):
        app.include_router(router, prefix=api_prefix)

    # 后台与前台都由后端直接输出 HTML：静态资源挂在各自的 base 下，页面路由紧随其后。
    # 没有任何前端构建产物，也不需要单独部署前端进程。
    admin_base = settings.admin_base
    portal_base = settings.portal_base
    admin_prefix = router_prefix(admin_base)
    portal_prefix = router_prefix(portal_base)

    # mount 必须先于页面路由注册，否则会被后台的 catch-all 404 吞掉
    app.mount(f"{admin_prefix}/static", StaticFiles(directory=str(STATIC_DIR)), name="admin_static")
    app.include_router(build_admin_router(admin_base), prefix=admin_prefix)

    portal_mounted = False
    if admin_base == "/":
        logger.warning("ADMIN_BASE=/ 时前台页面不会挂载，需要前台请把后台改到子路径（如 /admin）")
    else:
        if portal_prefix:
            app.mount(
                f"{portal_prefix}/static",
                StaticFiles(directory=str(STATIC_DIR)),
                name="portal_static",
            )
        app.include_router(build_portal_router(portal_base), prefix=portal_prefix)
        portal_mounted = True

    # 根路径按「前台 → 后台」的顺序找落点，老书签始终有去处
    if portal_mounted and portal_base != "/":

        @app.get("/", include_in_schema=False)
        async def portal_entry() -> RedirectResponse:
            return RedirectResponse(url=f"{base_path(portal_base)}")
    elif admin_base != "/":

        @app.get("/", include_in_schema=False)
        async def admin_entry() -> RedirectResponse:
            return RedirectResponse(url=f"{base_path(admin_base)}")

    return app


app = create_app()
