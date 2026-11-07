"""API Key 解析（内核层）。

鉴权依赖 ``app/core/authz.py`` 需要按 Key 定位凭证。这段逻辑原本在
``app.modules.sys.security.service`` 里，导致 core 反向依赖业务模块；
移到内核后，core → kernel 单向依赖成立。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.kernel.models import ApiKey

# 明文前缀长度：按前缀定位候选行，避免全表逐条比对哈希
API_KEY_PREFIX_LENGTH = 12


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def resolve_api_key(db: AsyncSession, raw: str, ip: str | None = None) -> ApiKey | None:
    """按 API Key 定位凭证；命中后刷新最近使用信息。前缀先定位候选，再逐条比哈希。"""
    if not raw:
        return None
    prefix = raw[:API_KEY_PREFIX_LENGTH]
    result = await db.execute(select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.status == "active"))
    candidates = list(result.scalars().all())
    matched = next((item for item in candidates if hash_api_key(raw) == item.key_hash), None)
    if matched is None:
        return None
    if matched.expires_at is not None and matched.expires_at <= utcnow():
        return None
    matched.last_used_at = utcnow()
    matched.last_used_ip = ip
    await db.commit()
    return matched
