from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ipgeo
from app.core.authz import AuthContext, scope_user_ids
from app.core.errors import AppError
from app.core.ipguard import (
    CompiledRule,
    display_target,
    hit_stats,
    invalidate_snapshot,
    match_rule,
    normalize_target,
)
from app.core.pagination import PageParams, page_data
from app.core.search import LIKE_ESCAPE, contains
from app.core.security import hash_password_async
from app.core.serializers import iso
from app.db.base import utcnow
from app.kernel.api_key import API_KEY_PREFIX_LENGTH as CORE_API_KEY_PREFIX_LENGTH
from app.kernel.api_key import hash_api_key
from app.kernel.db import commit
from app.kernel.models import ApiKey, IpRule, User
from app.kernel.models.credential import IP_RULE_STATUSES

# API Key 的解析与哈希已下沉到 app/kernel/api_key.py（core 鉴权层要用）
API_KEY_PREFIX_LENGTH = CORE_API_KEY_PREFIX_LENGTH
API_KEY_BODY_BYTES = 32
# 服务账号的用户名前缀。登录路径按这个前缀拒绝口令登录，
# 所以 Key 的服务账号从设计上就只能走 API Key 这一条路。
SERVICE_ACCOUNT_PREFIX = "svc_"


@dataclass(frozen=True)
class IpRuleInput:
    """``create_ip_rule`` 的入参；与 schemas 解耦，便于内部调用方（一键封禁、联动封禁）复用。"""

    target: str
    kind: str = "block"
    scope: str = "all"
    reason: str | None = None
    expires_at: Any = None


def ip_rule_out(item: IpRule, stats: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    stat = (stats or {}).get(item.id) or {}
    return {
        "id": item.id,
        "kind": item.kind,
        "target": display_target(item.target),
        "canonical_target": item.target,
        "scope": item.scope,
        "reason": item.reason,
        "status": item.status,
        "auto": item.auto,
        "expires_at": iso(item.expires_at),
        "created_by_name": item.created_by_name,
        "created_at": iso(item.created_at),
        "hit_count": int(stat.get("count") or 0),
        "last_hit_at": stat.get("last"),
    }


async def _audit(db: AsyncSession, ctx: AuthContext | None, action: str, detail: str) -> None:
    if ctx is None:
        # 系统自动触发的动作（如登录失败自动封禁）没有操作人，
        # 写进「谁做了什么」的操作日志只会污染审计，规则自身的 auto 标记已是记录。
        return
    from app.kernel.db import log_operation

    await log_operation(
        db,
        ctx,
        module="security",
        action=action,
        detail=detail,
        method="POST",
        path=f"/security/{action}",
    )


async def _commit_audit(db: AsyncSession, ctx: AuthContext | None, action: str, detail: str) -> None:
    """写业务数据与审计日志在同一个事务里：半条日志比没日志更难排查。"""
    await _audit(db, ctx, action, detail)
    await commit(db)
    await invalidate_snapshot()


# ------------------------------- IP 规则 -------------------------------


async def list_ip_rules(
    db: AsyncSession,
    params: PageParams,
    *,
    keyword: str | None = None,
    kind: str | None = None,
    scope: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    statement = select(IpRule)
    pattern = contains(keyword)
    if pattern is not None:
        statement = statement.where(
            or_(
                IpRule.target.like(pattern, escape=LIKE_ESCAPE),
                IpRule.reason.like(pattern, escape=LIKE_ESCAPE),
            )
        )
    if kind:
        statement = statement.where(IpRule.kind == kind)
    if scope:
        statement = statement.where(IpRule.scope == scope)
    if status:
        statement = statement.where(IpRule.status == status)
    statement = statement.order_by(IpRule.id.desc())

    total: int | None = None
    if params.total:
        total = int((await db.execute(select(func.count()).select_from(statement.subquery()))).scalar() or 0)
    rows = list((await db.execute(statement.offset(params.offset).limit(params.page_size))).scalars().all())
    stats = hit_stats()
    return page_data([ip_rule_out(item, stats) for item in rows], total, params)


async def get_ip_rule(db: AsyncSession, rule_id: int) -> dict[str, Any]:
    item = await db.get(IpRule, rule_id)
    if item is None:
        raise AppError("SECURITY_IP_RULE_NOT_FOUND", status_code=404)
    return ip_rule_out(item, hit_stats())


async def _ensure_unique(db: AsyncSession, target: str, scope: str, *, exclude_id: int | None = None) -> None:
    statement = select(IpRule.id).where(IpRule.target == target, IpRule.scope == scope)
    if exclude_id is not None:
        statement = statement.where(IpRule.id != exclude_id)
    exists = await db.execute(statement)
    if exists.scalar() is not None:
        raise AppError("SECURITY_IP_RULE_DUPLICATED", status_code=409)


async def create_ip_rule(
    db: AsyncSession,
    ctx: AuthContext | None,
    data: IpRuleInput,
    *,
    auto_commit: bool = True,
) -> dict[str, Any]:
    """新建 IP 规则。

    ``auto_commit=False`` 供「联动封禁」使用：调用方（``ban_user``）还有自己的
    业务字段与审计要写，规则必须和它们落在同一个事务里。否则规则先提交、封禁
    后失败时会留下一条无主的封禁规则，反过来封禁先提交也会让响应与库不一致。
    """
    await _ensure_unique(db, data.target, data.scope)
    item = IpRule(
        kind=data.kind,
        target=data.target,
        scope=data.scope,
        reason=data.reason,
        expires_at=data.expires_at,
        auto=ctx is None,
        created_by=None if ctx is None else ctx.user.id,
        created_by_name="system" if ctx is None else ctx.user.username,
    )
    db.add(item)
    await db.flush()
    await _audit(db, ctx, "ip-rule.create", f"rule_id={item.id} target={item.target} scope={item.scope}")
    if auto_commit:
        await commit(db)
        await invalidate_snapshot()
    await db.refresh(item)
    return ip_rule_out(item, hit_stats())


async def update_ip_rule(
    db: AsyncSession, ctx: AuthContext, rule_id: int, data: Any
) -> dict[str, Any]:
    item = await db.get(IpRule, rule_id)
    if item is None:
        raise AppError("SECURITY_IP_RULE_NOT_FOUND", status_code=404)
    payload = data.model_dump(exclude_unset=True)
    scope = payload.get("scope") or item.scope
    target = payload.get("target") or item.target
    if target != item.target or scope != item.scope:
        await _ensure_unique(db, target, scope, exclude_id=item.id)
    for field, value in payload.items():
        setattr(item, field, value)
    await db.flush()
    await _commit_audit(db, ctx, "ip-rule.update", f"rule_id={item.id}")
    await db.refresh(item)
    return ip_rule_out(item, hit_stats())


async def delete_ip_rule(db: AsyncSession, ctx: AuthContext, rule_id: int) -> None:
    item = await db.get(IpRule, rule_id)
    if item is None:
        raise AppError("SECURITY_IP_RULE_NOT_FOUND", status_code=404)
    target = item.target
    await db.delete(item)
    await db.flush()
    await _commit_audit(db, ctx, "ip-rule.delete", f"rule_id={rule_id} target={target}")


async def set_ip_rule_status(db: AsyncSession, ctx: AuthContext, rule_id: int, status: str) -> dict[str, Any]:
    if status not in IP_RULE_STATUSES:
        raise AppError("SECURITY_IP_RULE_STATUS_INVALID", status_code=422)
    item = await db.get(IpRule, rule_id)
    if item is None:
        raise AppError("SECURITY_IP_RULE_NOT_FOUND", status_code=404)
    item.status = status
    await db.flush()
    await _commit_audit(db, ctx, "ip-rule.status", f"rule_id={item.id} status={status}")
    await db.refresh(item)
    return ip_rule_out(item, hit_stats())


async def block_ip_now(
    db: AsyncSession,
    ctx: AuthContext | None,
    *,
    ip: str,
    scope: str = "all",
    reason: str | None = None,
    minutes: int = 0,
) -> dict[str, Any]:
    """一键封禁：单 IP 归一化成 /32；``minutes=0`` 表示永久封禁。"""
    expires_at = utcnow() + timedelta(minutes=minutes) if minutes else None
    return await create_ip_rule(
        db,
        ctx,
        IpRuleInput(
            target=ip,
            kind="block",
            scope=scope,
            reason=reason or "控制台一键封禁",
            expires_at=expires_at,
        ),
    )


async def locate_ip(db: AsyncSession, ip: str) -> dict[str, Any]:
    """IP 归属地 + 规则命中一屏看完。

    归属地回答「这个 IP 从哪来」，规则回答「它进得来吗」——排障时这两个问题
    总是一起出现，分两个接口查等于让运营自己拼线索。库缺失时 location 为 None，
    规则部分照常返回。
    """
    try:
        canonical = normalize_target(ip)
    except ValueError as exc:
        raise AppError("SECURITY_IP_INVALID", str(exc), 422) from exc
    address = display_target(canonical)
    location = await ipgeo.locate(address)
    return {
        "ip": address,
        "canonical": canonical,
        "location": location.as_dict() if location is not None else None,
        "rules": await lookup_ip(db, address),
        # 查询界面要显示「库加载了没有、用的什么策略」，否则归属地空白时无从判断原因
        "library": ipgeo.describe(),
    }


async def lookup_ip(db: AsyncSession, ip: str) -> dict[str, Any]:
    """排障用：这个 IP 在各作用域下会不会被拦、命中的是哪条规则。

    规则可以只作用于 api / admin / portal 之一，所以按作用域分别判定，
    只查 all 会漏掉「接口被封但页面能开」这种最常见的情况。
    """
    del db  # 命中判断只读规则快照，不查库
    try:
        canonical = normalize_target(ip)
    except ValueError as exc:
        raise AppError("SECURITY_IP_INVALID", str(exc), 422) from exc
    # match_rule 需要裸地址，canonical 形态的单 IP 带 /32、/128 后缀
    address = display_target(canonical)
    stats = hit_stats()
    scopes: dict[str, Any] = {}
    for name in ("api", "admin", "portal"):
        rule = await match_rule(address, name)
        scopes[name] = {
            "blocked": rule is not None,
            "rule": matched_rule_out(rule, stats) if rule is not None else None,
        }
    return {
        "ip": ip,
        "canonical": canonical,
        "blocked": any(item["blocked"] for item in scopes.values()),
        "scopes": scopes,
    }


# ------------------------------- 用户封禁 -------------------------------


def matched_rule_out(rule: CompiledRule, stats: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """命中规则的序列化；``CompiledRule`` 是中间件用的内存对象，字段比 ``IpRule`` 少。"""
    stat = (stats or {}).get(rule.id) or {}
    return {
        "id": rule.id,
        "kind": rule.kind,
        "target": display_target(rule.target),
        "canonical_target": rule.target,
        "scope": rule.scope,
        "reason": rule.reason,
        "expires_at": iso(rule.expires_at),
        "hit_count": int(stat.get("count") or 0),
        "last_hit_at": stat.get("last"),
    }


def banned_condition():
    """封禁中的用户过滤条件：已封禁且（永久或未到期）。

    SQL 侧判据必须与 ``User.is_banned`` 保持一致，否则列表会和登录拦截各说各话。
    """
    from sqlalchemy import or_

    return User.banned_at.is_not(None), or_(User.banned_until.is_(None), User.banned_until > utcnow())


def ban_state(user: User) -> dict[str, Any]:
    if not user.is_banned:
        return {"banned": False, "banned_until": None, "ban_reason": None, "banned_at": None}
    return {
        "banned": True,
        "banned_until": iso(user.banned_until),
        "ban_reason": user.ban_reason,
        "banned_at": iso(user.banned_at),
    }


async def ban_user(
    db: AsyncSession,
    ctx: AuthContext,
    user_id: int,
    *,
    reason: str | None = None,
    until: Any = None,
    ban_ip: bool = True,
) -> dict[str, Any]:
    from app.kernel.scope import ensure_user_access, revoke_user_sessions

    user = await db.get(User, user_id)
    await ensure_user_access(db, ctx, user)
    assert user is not None
    if user.id == ctx.user.id:
        raise AppError("SECURITY_SELF_BAN", status_code=409)
    if user.is_superuser and not ctx.is_superuser:
        raise AppError("SECURITY_BAN_SUPERUSER", status_code=403)

    user.banned_until = until
    user.ban_reason = reason or "违反使用规范"
    user.banned_at = utcnow()
    # 封禁必须立刻失效：撤掉全部会话并抬高 token_version，
    # 已签发但未过期的 access token 会因版本不匹配被鉴权依赖拒绝。
    await revoke_user_sessions(db, user.id)
    await db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    await db.flush()

    detail = f"user_id={user.id} until={until.isoformat() if until else 'permanent'}"
    ip_rule_id: int | None = None
    if ban_ip and user.last_login_ip:
        try:
            ip_rule = await create_ip_rule(
                db,
                ctx,
                IpRuleInput(
                    target=user.last_login_ip,
                    kind="block",
                    scope="all",
                    reason=f"账号 {user.username} 被封禁时联动封禁",
                    expires_at=until,
                ),
                # 与封禁同一个事务：规则不能单独提交，否则封禁失败会留下孤立规则
                auto_commit=False,
            )
            ip_rule_id = ip_rule["id"]
            detail += f" ip_rule_id={ip_rule_id}"
        except AppError as exc:
            if exc.code != "SECURITY_IP_RULE_DUPLICATED":
                raise
            # 该 IP 已经有封禁规则（同一出口 IP 的第二个账号被封）：复用既有规则。
            # 若把 409 直接抛给用户，封禁会连同已 flush 的封禁字段一起回滚，
            # 管理员看到的是「规则重复」而不是「用户已封禁」，功能实际不可用。
            existing = (
                await db.execute(
                    select(IpRule.id).where(
                        IpRule.target == user.last_login_ip, IpRule.scope == "all"
                    )
                )
            ).scalar()
            ip_rule_id = int(existing) if existing is not None else None
            detail += f" ip_rule_reused={ip_rule_id}"

    await _audit(db, ctx, "user.ban", detail)
    await commit(db)
    await invalidate_snapshot()
    return {"user_id": user.id, "ip_rule_id": ip_rule_id, **ban_state(user)}


async def unban_user(db: AsyncSession, ctx: AuthContext, user_id: int) -> dict[str, Any]:
    from app.kernel.scope import ensure_user_access

    user = await db.get(User, user_id)
    await ensure_user_access(db, ctx, user)
    assert user is not None
    user.banned_until = None
    user.ban_reason = None
    user.banned_at = None
    await db.flush()
    await _audit(db, ctx, "user.unban", f"user_id={user.id}")
    await commit(db)
    return {"user_id": user.id, **ban_state(user)}


async def list_banned_users(
    db: AsyncSession, ctx: AuthContext, params: PageParams, *, keyword: str | None = None
) -> dict[str, Any]:
    statement = select(User).where(*banned_condition())
    # 数据范围必须与其它用户列表一致：封禁列表里带着用户名、部门、登录 IP
    # 和归属地，不设范围等于给部门管理员开了一个跨部门侦察口。
    scoped = await scope_user_ids(db, ctx)
    if scoped is not None:
        statement = statement.where(User.id.in_(scoped))
    pattern = contains(keyword)
    if pattern is not None:
        statement = statement.where(
            or_(
                User.username.like(pattern, escape=LIKE_ESCAPE),
                User.nickname.like(pattern, escape=LIKE_ESCAPE),
            )
        )
    statement = statement.order_by(User.banned_at.desc())

    total: int | None = None
    if params.total:
        total = int((await db.execute(select(func.count()).select_from(statement.subquery()))).scalar() or 0)
    rows = list((await db.execute(statement.offset(params.offset).limit(params.page_size))).scalars().all())
    locations = await ipgeo.locate_many(item.last_login_ip for item in rows)
    items = [
        {
            "id": item.id,
            "username": item.username,
            "nickname": item.nickname,
            "dept_name": item.dept.name if item.dept else None,
            "status": item.status,
            "last_login_ip": item.last_login_ip,
            # 连带封禁的就是这个 IP，归属地决定了「这波攻击大概从哪来」
            "last_login_location": ipgeo.label_of(locations, item.last_login_ip),
            **ban_state(item),
        }
        for item in rows
    ]
    return page_data(items, total, params)


# -------------------------------- API Key --------------------------------


_hash_key = hash_api_key


def api_key_out(item: ApiKey) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "key_prefix": item.key_prefix,
        "scopes": [scope for scope in item.scopes.split(",") if scope],
        "status": item.status,
        "expires_at": iso(item.expires_at),
        "last_used_at": iso(item.last_used_at),
        "last_used_ip": item.last_used_ip,
        "remark": item.remark,
        "created_by_name": item.created_by_name,
        "created_at": iso(item.created_at),
    }


async def list_api_keys(
    db: AsyncSession, params: PageParams, *, keyword: str | None = None
) -> dict[str, Any]:
    statement = select(ApiKey)
    pattern = contains(keyword)
    if pattern is not None:
        statement = statement.where(ApiKey.name.like(pattern, escape=LIKE_ESCAPE))
    statement = statement.order_by(ApiKey.id.desc())
    total: int | None = None
    if params.total:
        total = int((await db.execute(select(func.count()).select_from(statement.subquery()))).scalar() or 0)
    rows = list((await db.execute(statement.offset(params.offset).limit(params.page_size))).scalars().all())
    return page_data([api_key_out(item) for item in rows], total, params)


async def _ensure_service_account(db: AsyncSession, name: str) -> User:
    """为 API Key 派生服务账号；同名 Key 复用同一个账号。

    Key 本身不是用户，但数据范围、审计署名、封禁都落在 ``sys_user`` 上。
    没有这个账号，Key 验过去也只是「一个能调接口的字符串」——既拿不到数据
    范围也写不进审计，鉴权链路断在半路。口令取随机值且不告诉任何人，
    等价于从设计上禁掉口令登录。
    """
    slug = "".join(ch if (ch.isalnum() or ch in "_.-") else "-" for ch in name.strip().lower())
    username = f"{SERVICE_ACCOUNT_PREFIX}{slug or 'key'}"[:64]
    normalized = username.lower()
    existing = (
        await db.execute(select(User).where(User.username_normalized == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    user = User(
        username=username,
        username_normalized=normalized,
        password_hash=await hash_password_async(secrets.token_urlsafe(48)),
        nickname=f"服务账号 {name.strip()}"[:64],
        status="active",
        is_superuser=False,
        must_change_password=False,
        remark="API Key 服务账号，由创建 Key 时自动派生；口令为随机值，禁止直接登录",
    )
    db.add(user)
    await db.flush()
    return user


def _normalize_scopes(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _validate_scopes(ctx: AuthContext, scopes: list[str]) -> None:
    """校验 Key 声明的权限码：必须真实存在，且创建人自己得持有。

    没有第二道，任何能建 Key 的人都能给自己签发一个带 ``*`` 的凭证，
    安全域就白做了。这和建用户时「只能指派自己够得着的角色」是同一条规矩。
    """
    from app.bootstrap.permissions import PERMISSION_CODES

    if not scopes:
        return
    unknown = sorted(set(scopes) - set(PERMISSION_CODES) - {"*"})
    if unknown:
        raise AppError(
            "SECURITY_API_KEY_SCOPE_UNKNOWN",
            status_code=422,
            params={"codes": ", ".join(unknown)},
        )
    if ctx.is_superuser or ctx.all_permissions:
        return
    beyond = sorted({scope for scope in scopes if scope != "*" and not ctx.has(scope)})
    if beyond or "*" in scopes:
        raise AppError(
            "SECURITY_API_KEY_SCOPE_FORBIDDEN",
            status_code=403,
            params={"codes": ", ".join(beyond) or "*"},
        )


async def create_api_key(db: AsyncSession, ctx: AuthContext, data: Any) -> dict[str, Any]:
    name = data.name.strip()
    await _ensure_api_key_unique(db, name)
    scopes = _normalize_scopes(data.scopes)
    _validate_scopes(ctx, scopes)
    raw = f"fck_{secrets.token_urlsafe(API_KEY_BODY_BYTES)}"
    # 先建服务账号再建 Key：Key 表的外键指向它，反过来会出现「Key 能用但没身份」的半成品
    service_user = await _ensure_service_account(db, name)
    item = ApiKey(
        name=name,
        key_prefix=raw[:API_KEY_PREFIX_LENGTH],
        key_hash=_hash_key(raw),
        scopes=",".join(scopes),
        expires_at=data.expires_at,
        remark=data.remark,
        created_by=ctx.user.id,
        created_by_name=ctx.user.username,
        user_id=service_user.id,
    )
    db.add(item)
    await db.flush()
    await _audit(db, ctx, "api-key.create", f"key_id={item.id} name={item.name} account={service_user.username}")
    await commit(db)
    await db.refresh(item)
    # 明文只在创建响应里出现一次，之后库里只剩 sha256
    return {**api_key_out(item), "key": raw}


async def _ensure_api_key_unique(db: AsyncSession, name: str, *, exclude_id: int | None = None) -> None:
    statement = select(ApiKey.id).where(ApiKey.name == name)
    if exclude_id is not None:
        statement = statement.where(ApiKey.id != exclude_id)
    exists = await db.execute(statement)
    if exists.scalar() is not None:
        raise AppError("SECURITY_API_KEY_DUPLICATED", status_code=409)


async def delete_api_key(db: AsyncSession, ctx: AuthContext, key_id: int) -> None:
    item = await db.get(ApiKey, key_id)
    if item is None:
        raise AppError("SECURITY_API_KEY_NOT_FOUND", status_code=404)
    name = item.name
    await db.delete(item)
    await db.flush()
    await _audit(db, ctx, "api-key.delete", f"key_id={key_id} name={name}")
    await commit(db)


async def set_api_key_status(db: AsyncSession, ctx: AuthContext, key_id: int, status: str) -> dict[str, Any]:
    if status not in ("active", "disabled"):
        raise AppError("SECURITY_API_KEY_STATUS_INVALID", status_code=422)
    item = await db.get(ApiKey, key_id)
    if item is None:
        raise AppError("SECURITY_API_KEY_NOT_FOUND", status_code=404)
    item.status = status
    await db.flush()
    await _audit(db, ctx, "api-key.status", f"key_id={item.id} status={status}")
    await commit(db)
    await db.refresh(item)
    return api_key_out(item)


