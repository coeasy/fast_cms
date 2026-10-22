from __future__ import annotations

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, client_ip, get_current_user, require_admin
from app.core.errors import AppError, success
from app.core.pagination import PageParams
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.security import service
from app.modules.security.schemas import (
    ApiKeyCreate,
    IpRuleBlockNow,
    IpRuleCreate,
    IpRuleUpdate,
    UserBanCreate,
)

router = protected_router(
    prefix="/security",
    tags=["安全管理"],
    dependencies=[Depends(require_admin)],
)


# ------------------------------- IP 规则 -------------------------------


@router.get("/ip-rules", summary="IP 规则列表", permission="security:ip-rule:list")
async def list_ip_rules(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=64),
    kind: str | None = Query(default=None, pattern="^(block|allow)$"),
    scope: str | None = Query(default=None, pattern="^(all|api|admin|portal)$"),
    status: str | None = Query(default=None, pattern="^(active|disabled|expired)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(
        await service.list_ip_rules(
            db,
            params,
            keyword=keyword,
            kind=kind,
            scope=scope,
            status=status,
        )
    )


@router.get("/ip-rules/{rule_id}", summary="IP 规则详情", permission="security:ip-rule:list")
async def get_ip_rule(
    rule_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_ip_rule(db, rule_id))


@router.post("/ip-rules", summary="新建 IP 规则", permission="security:ip-rule:create")
async def create_ip_rule(
    data: IpRuleCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(
        await service.create_ip_rule(
            db,
            ctx,
            service.IpRuleInput(
                target=data.target,
                kind=data.kind,
                scope=data.scope,
                reason=data.reason,
                expires_at=data.expires_at,
            ),
        ),
        "规则已创建",
    )


@router.put("/ip-rules/{rule_id}", summary="更新 IP 规则", permission="security:ip-rule:update")
async def update_ip_rule(
    rule_id: int,
    data: IpRuleUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_ip_rule(db, ctx, rule_id, data), "规则已更新")


@router.delete("/ip-rules/{rule_id}", summary="删除 IP 规则", permission="security:ip-rule:delete")
async def delete_ip_rule(
    rule_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_ip_rule(db, ctx, rule_id)
    return success(message="规则已删除")


@router.post("/ip-rules/{rule_id}/status", summary="启停 IP 规则", permission="security:ip-rule:update")
async def set_ip_rule_status(
    rule_id: int,
    status: str = Query(pattern="^(active|disabled)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(
        await service.set_ip_rule_status(db, ctx, rule_id, status), "规则状态已更新"
    )


@router.post("/ip-rules/block-now", summary="一键封禁 IP", permission="security:ip-rule:create")
async def block_ip_now(
    data: IpRuleBlockNow,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(
        await service.block_ip_now(
            db,
            ctx,
            ip=data.ip,
            scope=data.scope,
            reason=data.reason,
            minutes=data.minutes,
        ),
        "已封禁该 IP",
    )


@router.get("/ip-rules/lookup/{ip:path}", summary="查询 IP 命中规则", permission="security:ip-rule:list")
async def lookup_ip(
    ip: str,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.lookup_ip(db, ip))


# ------------------------------- IP 归属查询 -------------------------------


@router.get("/ip-locate/current", summary="查询当前请求的来源 IP", permission="security:ip-locate:list")
async def locate_current_ip(
    request: Request,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 运营最常问的一句是「我现在这个出口 IP 是什么、在哪」。
    # 背后有可信代理时这同时验证了 TRUST_PROXY 配得对不对：
    # 看到 127.0.0.1 说明没读 X-Forwarded-For，看到内网地址说明代理链少了一跳。
    ip = client_ip(request)
    if not ip:
        raise AppError("SECURITY_IP_UNKNOWN", "无法确定当前请求的来源 IP", 422)
    return success(await service.locate_ip(db, ip))


@router.get("/ip-locate/{ip:path}", summary="查询 IP 归属地与规则命中", permission="security:ip-locate:list")
async def locate_ip(
    ip: str,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.locate_ip(db, ip))


# ------------------------------- 用户封禁 -------------------------------


@router.get("/banned-users", summary="封禁用户列表", permission="security:user-ban:list")
async def list_banned_users(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_banned_users(db, params, keyword=keyword))


@router.post("/users/{user_id}/ban", summary="封禁用户", permission="security:user-ban:ban")
async def ban_user(
    user_id: int,
    data: UserBanCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(
        await service.ban_user(
            db,
            ctx,
            user_id,
            reason=data.reason,
            until=data.until,
            ban_ip=data.ban_ip,
        ),
        "用户已封禁",
    )


@router.post("/users/{user_id}/unban", summary="解封用户", permission="security:user-ban:ban")
async def unban_user(
    user_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.unban_user(db, ctx, user_id), "用户已解封")


# -------------------------------- API Key --------------------------------


@router.get("/api-keys", summary="API Key 列表", permission="security:api-key:list")
async def list_api_keys(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_api_keys(db, params, keyword=keyword))


@router.post("/api-keys", summary="创建 API Key", permission="security:api-key:create")
async def create_api_key(
    data: ApiKeyCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_api_key(db, ctx, data), "API Key 已创建，请立即保存明文")


@router.delete("/api-keys/{key_id}", summary="删除 API Key", permission="security:api-key:delete")
async def delete_api_key(
    key_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_api_key(db, ctx, key_id)
    return success(message="API Key 已删除")


@router.post("/api-keys/{key_id}/status", summary="启停 API Key", permission="security:api-key:update")
async def set_api_key_status(
    key_id: int,
    status: str = Query(pattern="^(active|disabled)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.set_api_key_status(db, ctx, key_id, status), "状态已更新")
