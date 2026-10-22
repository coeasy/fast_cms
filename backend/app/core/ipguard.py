"""IP / CIDR 访问控制守卫。

规则存在 ``sys_ip_rule``，中间件按请求路径判断作用域（api / admin / portal），
再拿客户端 IP 与生效规则做网段匹配。规则带 TTL 快照缓存，写入时显式失效，
所以封禁一条网段最多 5 秒内全节点生效，不需要重启。

设计取舍：

- **allow 优先于 block**：整段封禁后要给运维留白名单，否则会把自己关在外面；
- **网段在进程内预编译**：规则量通常很小（几十条），逐请求查库不可接受，
  因此启动后常驻编译结果，只把 canonical 字符串放进缓存；
- **命中统计只写缓存**：每个请求都去 update 一行会把写压力放大到不可用，
  统计是运营参考而非审计依据，丢了不致命。
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.authz import client_ip_from_scope
from app.core.cache import get_cache
from app.core.config import settings
from app.core.errors import error_payload, get_request_id
from app.core.tz import local_text
from app.db.base import utcnow
from app.modules.security.models import IP_RULE_KINDS, IP_RULE_SCOPES

logger = logging.getLogger("fastcms.security")

SNAPSHOT_KEY = "fastcms:ipguard:snapshot"
SNAPSHOT_TTL = 5
HIT_COUNT_PREFIX = "fastcms:ipguard:hit:"
HIT_TTL = 7 * 86400
IP_BLOCKED_CODE = "SECURITY_IP_BLOCKED"


@dataclass(frozen=True)
class CompiledRule:
    id: int
    kind: str
    scope: str
    target: str
    network: Any
    reason: str | None
    expires_at: datetime | None

    def matches(self, ip: ipaddress._BaseAddress) -> bool:
        return ip.version == self.network.version and ip in self.network


def normalize_target(value: str) -> str:
    """把用户输入归一化成 canonical IP / CIDR，非法值直接抛 ``ValueError``。"""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("地址不能为空")
    if "/" not in raw:
        raw = f"{raw}/128" if ":" in raw else f"{raw}/32"
    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise ValueError(f"不是合法的 IP 或 CIDR 网段：{value!r}") from exc
    return str(network)


def normalize_scope(value: str) -> str:
    scope = (value or "").strip().lower() or "all"
    if scope not in IP_RULE_SCOPES:
        raise ValueError(f"作用域只能是 {'/'.join(IP_RULE_SCOPES)}，收到：{value!r}")
    return scope


def normalize_kind(value: str) -> str:
    kind = (value or "").strip().lower()
    if kind not in IP_RULE_KINDS:
        raise ValueError(f"规则类型只能是 {'/'.join(IP_RULE_KINDS)}，收到：{value!r}")
    return kind


def display_target(target: str) -> str:
    """canonical 形式里单 IP 会被补成 /32、/128，展示时还原成用户熟悉的样子。"""
    for suffix in ("/32", "/128"):
        if target.endswith(suffix):
            return target[: -len(suffix)]
    return target


@lru_cache(maxsize=512)
def _compile_network(target: str) -> Any:
    return ipaddress.ip_network(target, strict=False)


@lru_cache(maxsize=512)
def _parse_address(ip: str) -> Any:
    return ipaddress.ip_address(ip)


def request_scope(path: str) -> str:
    """按路径判断该请求属于哪个作用域，决定哪些规则要参与匹配。"""
    prefix = settings.api_prefix
    if prefix and path.startswith(prefix):
        return "api"
    admin = settings.admin_base
    if admin and admin != "/" and (path == admin or path.startswith(f"{admin}/")):
        return "admin"
    portal = settings.portal_base
    if portal and portal != "/" and (path == portal or path.startswith(f"{portal}/")):
        return "portal"
    return "all"


# 守卫自身的管理端点，block 规则一律放行。
# 少了这条通道，一旦把/api/v1/security/ip-rules 所在网段封掉，连删规则的那个请求
# 都进不来——产品内没有任何自救手段，只能直连数据库改表，封禁就从控制变成了事故。
# 这不构成绕过：被封的请求照样要过鉴权，没有管理员会话仍然是 401；
# 有管理员会话的人本来就能改一切。allow 规则也不受影响，它本身就是白名单。
MANAGEMENT_SUFFIXES: tuple[str, ...] = ("ip-rules", "banned-users")


def is_management_path(path: str) -> bool:
    """判断是否是 IP 守卫的管理端点。"""
    prefix = settings.api_prefix
    for suffix in MANAGEMENT_SUFFIXES:
        candidate = f"{prefix}/security/{suffix}"
        if path == candidate or path.startswith(f"{candidate}/"):
            return True
    return False


def _serialize(rule: CompiledRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "kind": rule.kind,
        "scope": rule.scope,
        "target": rule.target,
        "reason": rule.reason,
        "expires_at": rule.expires_at.isoformat() if rule.expires_at else None,
    }


def _deserialize(item: dict[str, Any]) -> CompiledRule:
    expires = item.get("expires_at")
    return CompiledRule(
        id=int(item["id"]),
        kind=str(item["kind"]),
        scope=str(item["scope"]),
        target=str(item["target"]),
        network=_compile_network(str(item["target"])),
        reason=item.get("reason"),
        expires_at=datetime.fromisoformat(expires) if expires else None,
    )


async def load_snapshot() -> list[CompiledRule]:
    """读取生效中的规则；缓存未命中时查库，并顺带清理掉已过期的临时封禁。"""
    from sqlalchemy import select

    from app.db.base import utcnow
    from app.db.session import async_session_factory
    from app.modules.security.models import IpRule

    cache = get_cache()
    cached = await cache.get(SNAPSHOT_KEY)
    if cached is not None:
        return [_deserialize(item) for item in cached]

    now = utcnow()
    compiled: list[CompiledRule] = []
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(IpRule).where(IpRule.status == "active").order_by(IpRule.id.asc())
            )
            rows = list(result.scalars().all())
    except Exception:  # noqa: BLE001
        # 守卫故障不能把整站打死：查不到规则就当没有规则，业务接口继续可用
        logger.exception("ipguard 规则加载失败，本次按「无规则」放行")
        return []

    expired: list[int] = []
    for row in rows:
        if row.expires_at is not None and row.expires_at <= now:
            expired.append(row.id)
            continue
        try:
            network = _compile_network(row.target)
        except ValueError:
            # 历史脏数据（手工改库写进非法网段）不能让每次请求都崩
            logger.warning("ipguard 跳过非法网段规则 id=%s target=%r", row.id, row.target)
            continue
        compiled.append(
            CompiledRule(
                id=row.id,
                kind=row.kind,
                scope=row.scope,
                target=row.target,
                network=network,
                reason=row.reason,
                expires_at=row.expires_at,
            )
        )

    if expired:
        await _expire_rules(expired)

    await cache.set(SNAPSHOT_KEY, [_serialize(item) for item in compiled], ttl=SNAPSHOT_TTL)
    return compiled


async def _expire_rules(ids: list[int]) -> None:
    from sqlalchemy import update

    from app.db.session import async_session_factory
    from app.modules.security.models import IpRule

    try:
        async with async_session_factory() as db:
            await db.execute(update(IpRule).where(IpRule.id.in_(ids)).values(status="expired"))
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("ipguard 过期规则状态回写失败 ids=%s", ids)


async def invalidate_snapshot() -> None:
    """规则变更后调用，让新规则尽快生效（最多再等一个 TTL）。"""
    await get_cache().delete(SNAPSHOT_KEY)


async def match_rule(ip: str, scope: str) -> CompiledRule | None:
    """返回命中的拦截规则；``None`` 表示放行。allow 规则优先。"""
    try:
        address = _parse_address(ip)
    except ValueError:
        return None
    blocked: CompiledRule | None = None
    for rule in await load_snapshot():
        if rule.scope not in (scope, "all"):
            continue
        if not rule.matches(address):
            continue
        if rule.kind == "allow":
            return None
        if blocked is None:
            blocked = rule
    return blocked


async def record_hit(rule: CompiledRule) -> None:
    """命中统计写缓存，不落库：请求路径上不能引入写放大。"""
    cache = get_cache()
    try:
        await cache.incr(f"{HIT_COUNT_PREFIX}{rule.id}:count", HIT_TTL)
        await cache.set(
            f"{HIT_COUNT_PREFIX}{rule.id}:last",
            local_text(utcnow(), "%Y-%m-%d %H:%M:%S%z"),
            ttl=HIT_TTL,
        )
    except Exception:  # noqa: BLE001
        logger.debug("ipguard 命中统计写入失败 rule_id=%s", rule.id)


def hit_stats() -> dict[int, dict[str, Any]]:
    """命中统计。内存缓存下可精确读取；Redis 下没有 SCAN 能力，返回空让管理页显示「—」。"""
    import time

    from app.core.cache import MemoryCache

    cache = get_cache()
    if not isinstance(cache, MemoryCache):
        return {}

    stats: dict[int, dict[str, Any]] = {}
    now = time.time()
    prefix_length = len(HIT_COUNT_PREFIX)
    for key, (expires, value) in list(cache._store.items()):
        if not key.startswith(HIT_COUNT_PREFIX):
            continue
        if expires is not None and expires < now:
            continue
        rule_id_text, _, field = key[prefix_length:].partition(":")
        if not rule_id_text.isdigit():
            continue
        entry = stats.setdefault(int(rule_id_text), {"count": 0, "last": None})
        if field == "count":
            entry["count"] = int(value or 0)
        elif field == "last":
            entry["last"] = value
    return stats


def _describe(rule: CompiledRule) -> str:
    scope_text = {"all": "整站", "api": "接口", "admin": "后台", "portal": "前台"}.get(rule.scope, rule.scope)
    reason = rule.reason or "未填写原因"
    return f"当前网络（{rule.target}）已被限制访问{scope_text}：{reason}"


def _blocked_response(request: Request, rule: CompiledRule) -> Any:
    if request_scope(request.url.path) == "api" or request.url.path.startswith(settings.api_prefix):
        return JSONResponse(
            status_code=403,
            content=error_payload(
                IP_BLOCKED_CODE,
                "当前网络已被限制访问，如有疑问请联系管理员",
                {"rule_id": rule.id, "reason": rule.reason, "request_id": get_request_id()},
            ),
            headers={"Retry-After": "0"},
        )
    return _blocked_page(request, rule)


def _blocked_page(request: Request, rule: CompiledRule) -> HTMLResponse:
    from app.web.router import templates

    return templates.TemplateResponse(
        request=request,
        name="pages/error.html",
        context={
            "request": request,
            "base": request.scope.get("root_path", "") or "/",
            "admin_base": _base_path(settings.admin_base),
            "portal_base": _base_path(settings.portal_base),
            "api_prefix": settings.api_prefix,
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "page_title": "访问被拒绝",
            "page_subtitle": "",
            "page_path": request.url.path,
            "page_key": "error",
            "code": "403",
            "message": "访问被拒绝",
            "detail": _describe(rule),
        },
        status_code=403,
    )


def _base_path(base: str) -> str:
    return base if base.endswith("/") else f"{base}/"


class IpGuardMiddleware:
    """按 IP / CIDR 规则拦截请求。

    纯 ASGI 实现，理由同 ``RequestContextMiddleware``：``BaseHTTPMiddleware``
    会破坏流式请求体，multipart 上传在真实 ASGI 服务器下必然死锁。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        ip = client_ip_from_scope(scope)
        path = scope.get("path", "")
        if ip:
            try:
                rule = await match_rule(ip, request_scope(path))
            except Exception:  # noqa: BLE001
                logger.exception("ipguard 匹配失败，按放行处理 ip=%s", ip)
                rule = None
            if rule is not None:
                await record_hit(rule)
                if not is_management_path(path):
                    logger.warning(
                        "ipguard blocked ip=%s path=%s rule_id=%s kind=%s target=%s",
                        ip,
                        path,
                        rule.id,
                        rule.kind,
                        rule.target,
                    )
                    response = _blocked_response(Request(scope, receive), rule)
                    await response(scope, receive, send)
                    return
                logger.info(
                    "ipguard 管理端点放行 ip=%s path=%s rule_id=%s（保留自救通道）",
                    ip,
                    path,
                    rule.id,
                )

        await self.app(scope, receive, send)
