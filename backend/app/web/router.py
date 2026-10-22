"""HTML 页面路由。

页面全部由 Jinja2 在服务端渲染，浏览器只加载同源的 CSS/ES Module，
没有构建步骤、没有 dist 产物、没有独立的前端服务。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings

WEB_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


# 页面清单：path 是挂载在 ADMIN_BASE 之后的路径，script 对应 static/js/pages/<name>.js
PAGES: tuple[dict[str, Any], ...] = (
    {
        "path": "/login",
        "template": "pages/login.html",
        "title": "登录",
        "page": "login",
        "public": True,
    },
    {
        "path": "/dashboard",
        "template": "pages/dashboard.html",
        "title": "工作台",
        "subtitle": "系统运行概览与近期动态",
        "page": "dashboard",
    },
    {
        "path": "/system/user",
        "template": "pages/system/user.html",
        "title": "用户管理",
        "subtitle": "账号、部门与角色归属",
        "page": "system/user",
    },
    {
        "path": "/system/role",
        "template": "pages/system/role.html",
        "title": "角色管理",
        "subtitle": "角色、权限与数据范围",
        "page": "system/role",
    },
    {
        "path": "/system/menu",
        "template": "pages/system/menu.html",
        "title": "菜单管理",
        "subtitle": "导航结构、按钮权限与可见性",
        "page": "system/menu",
    },
    {
        "path": "/system/dept",
        "template": "pages/system/dept.html",
        "title": "部门管理",
        "subtitle": "组织架构与层级关系",
        "page": "system/dept",
    },
    {
        "path": "/system/permission",
        "template": "pages/system/permission.html",
        "title": "权限清单",
        "subtitle": "后端注册的权限码，按模块分组",
        "page": "system/permission",
    },
    {
        "path": "/system/dict",
        "template": "pages/system/dict.html",
        "title": "字典管理",
        "subtitle": "字典类型与字典项",
        "page": "system/dict",
    },
    {
        "path": "/system/config",
        "template": "pages/system/config.html",
        "title": "系统参数",
        "subtitle": "运行期配置项与公开参数",
        "page": "system/config",
    },
    {
        "path": "/system/theme",
        "template": "pages/system/theme.html",
        "title": "主题模板",
        "subtitle": "配色、布局与密度",
        "page": "system/theme",
    },
    {
        "path": "/monitor/online",
        "template": "pages/monitor/online.html",
        "title": "在线用户",
        "subtitle": "当前活跃会话与强制下线",
        "page": "monitor/online",
    },
    {
        "path": "/monitor/server",
        "template": "pages/monitor/server.html",
        "title": "服务器监控",
        "subtitle": "运行环境、资源占用与进程信息",
        "page": "monitor/server",
    },
    {
        "path": "/monitor/login-log",
        "template": "pages/monitor/login_log.html",
        "title": "登录日志",
        "subtitle": "登录成功与失败记录",
        "page": "monitor/login-log",
    },
    {
        "path": "/monitor/operation-log",
        "template": "pages/monitor/operation_log.html",
        "title": "操作日志",
        "subtitle": "写操作审计与请求详情",
        "page": "monitor/operation-log",
    },
    {
        "path": "/schedule/job",
        "template": "pages/schedule/job.html",
        "title": "任务管理",
        "subtitle": "注册表任务与 Python 脚本任务",
        "page": "schedule/job",
    },
    {
        "path": "/schedule/template",
        "template": "pages/schedule/template.html",
        "title": "任务模板",
        "subtitle": "可复用的任务定义",
        "page": "schedule/template",
    },
    {
        "path": "/schedule/log",
        "template": "pages/schedule/log.html",
        "title": "执行日志",
        "subtitle": "任务运行结果与输出",
        "page": "schedule/log",
    },
    {
        "path": "/content/notice",
        "template": "pages/content/notice.html",
        "title": "公告管理",
        "subtitle": "公告草稿、发布与置顶",
        "page": "content/notice",
    },
    {
        "path": "/notify/channel",
        "template": "pages/notify/channel.html",
        "title": "通知渠道",
        "subtitle": "钉钉 / 飞书 / 企业微信 / Webhook",
        "page": "notify/channel",
    },
    {
        "path": "/notify/record",
        "template": "pages/notify/record.html",
        "title": "发送记录",
        "subtitle": "推送结果、重试与清理",
        "page": "notify/record",
    },
    {
        "path": "/tool/file",
        "template": "pages/tool/file.html",
        "title": "文件管理",
        "subtitle": "上传、下载与删除",
        "page": "tool/file",
    },
    {
        "path": "/tool/codegen",
        "template": "pages/tool/codegen.html",
        "title": "代码生成",
        "subtitle": "按数据表生成 CRUD 代码",
        "page": "tool/codegen",
    },
    {
        "path": "/security/ip-rule",
        "template": "pages/security/ip_rule.html",
        "title": "IP 访问控制",
        "subtitle": "按 IP / 网段与作用域封禁或放行请求",
        "page": "security/ip-rule",
    },
    {
        "path": "/security/ban",
        "template": "pages/security/ban.html",
        "title": "用户封禁",
        "subtitle": "封禁中的账号、解封与 IP 连带封禁",
        "page": "security/ban",
    },
    {
        "path": "/security/api-key",
        "template": "pages/security/api_key.html",
        "title": "API Key",
        "subtitle": "第三方集成的访问凭证，明文仅创建时可见",
        "page": "security/api-key",
    },
    {
        "path": "/security/ip-locate",
        "template": "pages/security/ip_locate.html",
        "title": "IP 归属查询",
        "subtitle": "离线查询 IP 归属地，并查看命中的封禁 / 放行规则",
        "page": "security/ip-locate",
    },
    {
        "path": "/account/profile",
        "template": "pages/account/profile.html",
        "title": "个人中心",
        "subtitle": "资料、头像与登录设备",
        "page": "account/profile",
    },
    {
        "path": "/account/password",
        "template": "pages/account/password.html",
        "title": "修改密码",
        "subtitle": "修改后所有会话将失效",
        "page": "account/password",
    },
    {
        "path": "/403",
        "template": "pages/error.html",
        "title": "无权访问",
        "page": "error",
        "public": True,
        "status": 403,
        "code": "403",
        "message": "没有权限访问该页面",
        "detail": "当前账号未被授予此页面的访问权限，请联系管理员分配角色。",
    },
    {
        "path": "/404",
        "template": "pages/error.html",
        "title": "页面不存在",
        "page": "error",
        "public": True,
        "status": 404,
        "code": "404",
        "message": "页面不存在",
        "detail": "链接可能已失效，或页面已被移动到其他位置。",
    },
)


def base_path(value: str) -> str:
    """把挂载路径归一化成带尾斜杠的前缀，根路径保持 "/"。"""
    raw = (value or "/").strip()
    if raw in ("", "/"):
        return "/"
    return f"/{raw.strip('/')}/"


def router_prefix(value: str) -> str:
    """APIRouter 的 prefix：根路径必须是空串，否则会拼出 //path。"""
    raw = (value or "/").strip()
    return "" if raw in ("", "/") else f"/{raw.strip('/')}"


def _common_context(request: Request, admin_base: str, page: dict[str, Any]) -> dict[str, Any]:
    return {
        "request": request,
        "base": base_path(admin_base),
        "admin_base": base_path(admin_base),
        "portal_base": base_path(settings.portal_base),
        "api_prefix": settings.api_prefix,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "page_title": page["title"],
        "page_subtitle": page.get("subtitle", ""),
        "page_path": page["path"],
        "page_key": page["page"],
        "code": page.get("code", ""),
        "message": page.get("message", ""),
        "detail": page.get("detail", ""),
    }


def build_admin_router(admin_base: str) -> APIRouter:
    """后台页面路由；由 main 按 ADMIN_BASE 挂载。"""
    router = APIRouter()

    def render(page: dict[str, Any], request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name=page["template"],
            context=_common_context(request, admin_base, page),
            status_code=int(page.get("status", 200)),
        )

    for page in PAGES:
        def endpoint(request: Request, _page: dict[str, Any] = page) -> HTMLResponse:
            return render(_page, request)

        router.add_api_route(
            page["path"],
            endpoint,
            methods=["GET"],
            include_in_schema=False,
            name=f"web{page['path'].replace('/', '_')}",
        )

    @router.get("/", include_in_schema=False, name="web_admin_root")
    async def admin_root() -> RedirectResponse:
        return RedirectResponse(url=f"{base_path(admin_base)}dashboard")

    @router.get("/{path:path}", include_in_schema=False, name="web_admin_not_found")
    async def admin_not_found(request: Request, path: str) -> HTMLResponse:
        page = {
            "path": f"/{path}",
            "template": "pages/error.html",
            "title": "页面不存在",
            "page": "error",
            "status": 404,
            "code": "404",
            "message": "页面不存在",
            "detail": f"后台没有 /{path} 这个页面。",
        }
        return render(page, request)

    return router


def build_portal_router(portal_base: str) -> APIRouter:
    """前台站点：首页 + 兜底 404，同样由后端直接输出 HTML。"""
    router = APIRouter()
    admin_base = base_path(settings.admin_base)

    def context(request: Request, page: dict[str, Any]) -> dict[str, Any]:
        return {
            "request": request,
            "base": base_path(portal_base),
            "admin_base": admin_base,
            "portal_base": base_path(portal_base),
            "api_prefix": settings.api_prefix,
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "page_title": page["title"],
            "page_path": page["path"],
            "page_key": page["page"],
            "code": page.get("code", ""),
            "message": page.get("message", ""),
            "detail": page.get("detail", ""),
        }

    @router.get("/", include_in_schema=False, name="web_portal_home")
    async def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="portal/home.html",
            context=context(request, {"title": "首页", "path": "/", "page": "portal/home"}),
        )

    @router.get("/{path:path}", include_in_schema=False, name="web_portal_not_found")
    async def not_found(request: Request, path: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="pages/error.html",
            context=context(
                request,
                {
                    "title": "页面不存在",
                    "path": f"/{path}",
                    "page": "error",
                    "code": "404",
                    "message": "页面不存在",
                    "detail": f"前台站点没有 /{path} 这个页面。",
                },
            ),
            status_code=404,
        )

    return router
