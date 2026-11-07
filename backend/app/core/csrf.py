"""Cookie 认证接口的同源（Origin/Referer）校验。

P0-1 组合策略（与 ``SameSite=Lax`` 组成纵深防御）：

- **默认同源部署**：浏览器对 Cookie 认证接口（refresh / logout）发起跨站请求时，
  现代浏览器因 ``SameSite=Lax`` 不会附带 Cookie；此处再叠加 Origin/Referer 校验，
  即使同站子域或历史浏览器场景也不会被跨源请求驱动。
- **分域部署**（配置了 ``APP_CORS_ORIGINS``）：跨域调用方必须出现在显式允许列表中，
  否则即使携带 Cookie 也会被拒绝，避免「配置了 CORS 却绕过同源防护」的漏洞。
- **无 Origin/Referer 的请求**视为非浏览器客户端（curl / 脚本 / 服务间调用），不做拦截，
  因为它们本身不具备浏览器 Cookie 的跨站携带语义，不属于 CSRF 威胁面。

同源判定以 host:port（netloc）为准，但**只单向放宽 scheme**：TLS 在反向代理
终止时浏览器以 ``https://`` 发起、应用收到的是 ``http``，允许这一组合；反向
不允许（``https`` 应用不接受 ``http`` 来源），否则同主机的降级页面就能驱动
Cookie 认证接口，把纵深防御削掉一半。跨站攻击者换的是 host，netloc 比较阻断。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.core.config import settings


def _parse_origin(raw: str) -> tuple[str, str] | None:
    """把 Origin/Referer 解析成 ``(scheme, netloc)``；畸形头返回 ``None``。

    ``urlsplit`` 遇到非法 IPv6（如 ``http://[::1``）会抛 ``ValueError``。
    Origin 是客户端可控的头，不接住就会变成 refresh/logout 上的匿名 500。
    """
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    return parts.scheme.lower(), parts.netloc.lower()


def _raw_origin(request: Request) -> str | None:
    return request.headers.get("origin") or request.headers.get("referer")


def request_origin(request: Request) -> str | None:
    """从 ``Origin`` 优先、``Referer`` 兜底解析请求来源。

    返回规范化的 ``scheme://netloc``；两者皆无或格式非法返回 ``None``。
    """
    raw = _raw_origin(request)
    if not raw:
        return None
    parsed = _parse_origin(raw)
    if parsed is None:
        return None
    return f"{parsed[0]}://{parsed[1]}"


def is_same_origin(request: Request) -> bool:
    """请求来源是否被允许访问 Cookie 认证接口。返回 ``False`` 表示应拒绝（跨源）。"""
    raw = _raw_origin(request)
    if not raw:
        # 非浏览器客户端：无跨站 Cookie 携带语义，放行
        return True
    origin = request_origin(request)
    if origin is None:
        # 带了来源头但解析不了：明确拒绝，不按「没有 Origin」放行
        return False
    scheme, _, netloc = origin.partition("://")
    request_url = urlsplit(str(request.url))
    request_scheme = request_url.scheme.lower()
    if netloc == (request_url.netloc or "").lower() and (
        scheme == request_scheme or (scheme == "https" and request_scheme == "http")
    ):
        return True
    # 分域部署：显式配置的允许来源（配置项自带 scheme，按完整 origin 精确匹配）
    allowed = {item.rstrip("/").lower() for item in settings.cors_origins}
    return origin in allowed


def ensure_same_origin(request: Request) -> None:
    """同源校验不通过时抛出跨源拒绝异常（403）。"""
    from app.core.errors import AppError

    if not is_same_origin(request):
        raise AppError(
            "AUTH_ORIGIN_DENIED",
            status_code=403,
        )
