"""服务端渲染的 HTML 前端。

整个后台与前台都由 FastAPI 直接输出 HTML（Jinja2 模板 + 原生 JS），
不存在 Node 构建产物，也不需要单独部署前端进程。
"""

from app.web.router import WEB_DIR, base_path, build_admin_router, build_portal_router, router_prefix

__all__ = [
    "WEB_DIR",
    "base_path",
    "build_admin_router",
    "build_portal_router",
    "router_prefix",
]
