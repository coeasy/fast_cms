"""应用装配：异常处理器、中间件、模块路由与静态/页面挂载（A-6）。

原来这些都挤在 ``main.py``（271 行），与探活端点、启动引导混在一起。
拆出后 ``app.main`` 只保留 ``create_app()`` 与 ``lifespan``；本模块负责
「把东西装到 app 上」，``app.health`` 负责探活与元信息端点。
"""

from __future__ import annotations

import logging
import mimetypes

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    error_payload,
    get_request_id,
    http_error_handler,
    integrity_error_handler,
    validation_error_handler,
)
from app.core.ipguard import IpGuardMiddleware
from app.core.middleware import OperationLogMiddleware, RequestContextMiddleware
from app.core.module import module_routers
from app.core.ratelimit import RateLimitMiddleware
from app.web import WEB_DIR, base_path, build_admin_router, build_portal_router, router_prefix
from app.web.assets import VersionedStaticFiles

logger = logging.getLogger("fastcms")

# 服务端渲染的 HTML 前端资源目录（CSS / 原生 JS），随后端一起发布
STATIC_DIR = WEB_DIR / "static"

# Windows registry may map .js to text/plain, which makes browsers reject the SPA bundle.
for _extension, _content_type in (
    (".js", "text/javascript"),
    (".mjs", "text/javascript"),
    (".css", "text/css"),
    (".svg", "image/svg+xml"),
    (".woff2", "font/woff2"),
):
    mimetypes.add_type(_content_type, _extension)


def register_exception_handlers(app: FastAPI) -> None:
    """注册四类已知异常处理器 + 兜底 500。

    唯一约束 / 外键冲突统一收敛到全局 ``integrity_error_handler``（A-6）：
    ``kernel.db.commit()`` 只做「提交 + 语义化异常」，不重复做 HTTP 层翻译；
    直接 ``await db.commit()`` 的调用点由这一层兜底，两侧语义一致（409 DATA_CONFLICT）。
    """
    # Starlette 的处理程序签名声明收 Exception，但运行时按注册的异常类传入 AppError
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    # 先查后插的预检挡不住并发，数据库约束才是最终防线；
    # 不接这一层，客户端会把「数据重复」看成「服务器内部错误」。
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    @app.exception_handler(Exception)
    async def log_unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error request_id=%s", get_request_id(), exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_payload("INTERNAL_ERROR", "服务器内部错误，请使用 request_id 联系管理员"),
        )


def register_middleware(app: FastAPI) -> None:
    """CORS + 四层中间件。

    ``add_middleware`` 是「后注册者在外层」，注册顺序（CORS → OperationLog →
    RateLimit → IpGuard → RequestContext）对应的**实际栈序（外 → 内）**是：

        RequestContext → IpGuard → RateLimit → OperationLog → CORS → 业务路由

    - ``RequestContext`` 必须最外层：请求上下文与 request_id 是其余中间件
      打慢请求日志、写审计的依赖，放在内层就拿不到；
    - ``IpGuard`` 次之：被封禁的请求不该再进入限流、审计与业务逻辑；
    - ``OperationLog`` 在 CORS 内层：预检 OPTIONS 请求不该写审计。

    这一顺序由 ``tests/integration/test_middleware_stack.py`` 断言锁死——
    注释会腐化，测试不会。
    """
    if settings.cors_origins:
        # P0-2：凭据模式下禁止 `*` 来源（见 config._check_cors_origins）；
        # methods/headers 白名单化，只放行前端实际使用与预检必需的项。
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "Accept", "X-Requested-With", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )
    app.add_middleware(OperationLogMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(IpGuardMiddleware)
    app.add_middleware(RequestContextMiddleware)


def include_module_routers(app: FastAPI, api_prefix: str) -> None:
    """按模块清单（含 codegen，A-2 已收编）的 order 顺序注册业务路由。

    新增模块只需在自己的子包里放 ``module.py``，不必再改这里。
    """
    for router in module_routers():
        app.include_router(router, prefix=api_prefix)


def mount_web(app: FastAPI) -> None:
    """后台与前台静态资源 + 页面路由 + 根路径重定向。

    都由后端直接输出 HTML：静态资源挂在各自的 base 下，页面路由紧随其后。
    没有任何前端构建产物，也不需要单独部署前端进程。
    """
    admin_base = settings.admin_base
    portal_base = settings.portal_base
    admin_prefix = router_prefix(admin_base)
    portal_prefix = router_prefix(portal_base)

    # mount 必须先于页面路由注册，否则会被后台的 catch-all 404 吞掉
    app.mount(f"{admin_prefix}/static", VersionedStaticFiles(directory=str(STATIC_DIR)), name="admin_static")
    app.include_router(build_admin_router(admin_base), prefix=admin_prefix)

    portal_mounted = False
    if admin_base == "/":
        logger.warning("ADMIN_BASE=/ 时前台页面不会挂载，需要前台请把后台改到子路径（如 /admin）")
    else:
        if portal_prefix:
            app.mount(
                f"{portal_prefix}/static",
                VersionedStaticFiles(directory=str(STATIC_DIR)),
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
