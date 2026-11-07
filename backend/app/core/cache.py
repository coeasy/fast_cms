from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger("fastcms.cache")


class MemoryCache:
    """进程内缓存。带 TTL 的键（限流计数、字典、参数）会随客户端/配置变化无限增长，
    因此写入时按 ``CACHE_MAX_ITEMS`` 兜底清理过期项并按插入顺序淘汰，避免内存无上限。"""

    def __init__(self, max_items: int | None = None) -> None:
        self._store: dict[str, tuple[float | None, Any]] = {}
        self._lock = asyncio.Lock()
        self._max_items = max(64, max_items or settings.cache_max_items)

    def _prune_locked(self) -> None:
        if len(self._store) < self._max_items:
            return
        now = time.time()
        expired = [key for key, (expires, _) in self._store.items() if expires is not None and expires < now]
        for key in expired:
            self._store.pop(key, None)
        overflow = len(self._store) - self._max_items
        if overflow <= 0:
            return
        # 无 TTL 的键排在前面会被优先淘汰，但字典/参数键都带 TTL，实际淘汰的是过期计数与陈旧缓存
        for key in list(self._store)[:overflow]:
            self._store.pop(key, None)

    async def get(self, key: str) -> Any:
        async with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires, value = item
            if expires is not None and expires < time.time():
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        async with self._lock:
            expires = time.time() + ttl if ttl else None
            self._store[key] = (expires, value)
            self._prune_locked()

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def incr(self, key: str, ttl: int) -> int:
        async with self._lock:
            now = time.time()
            item = self._store.get(key)
            if not item or (item[0] is not None and item[0] < now):
                self._store[key] = (now + ttl, 1)
                self._prune_locked()
                return 1
            expires, value = item
            value = int(value) + 1
            self._store[key] = (expires, value)
            return value

    async def acquire_lock(self, key: str, ttl: int) -> bool:
        async with self._lock:
            now = time.time()
            item = self._store.get(key)
            if item and (item[0] is None or item[0] >= now):
                return False
            self._store[key] = (now + ttl, "1")
            return True

    async def release_lock(self, key: str) -> None:
        await self.delete(key)

    async def close(self) -> None:
        self._store.clear()

    async def ping(self) -> bool:
        return True


class RedisCache:
    def __init__(self, url: str) -> None:
        from redis.asyncio import from_url

        self.client = from_url(url, decode_responses=True)

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def get(self, key: str) -> Any:
        value = await self.client.get(key)
        return json.loads(value) if value is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self.client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)

    async def delete(self, key: str) -> None:
        await self.client.delete(key)

    async def incr(self, key: str, ttl: int) -> int:
        # 占位写入带 TTL，避免 INCR 与 EXPIRE 之间崩溃留下永不过期的计数器；
        # ttl < 0 兜住历史遗留的无过期时间 key，否则限流窗口永远不重置
        await self.client.set(key, "0", ex=ttl, nx=True)
        value = await self.client.incr(key)
        if int(value) > 1 and await self.client.ttl(key) < 0:
            await self.client.expire(key, ttl)
        return int(value)

    async def acquire_lock(self, key: str, ttl: int) -> bool:
        return bool(await self.client.set(key, "1", ex=ttl, nx=True))

    async def release_lock(self, key: str) -> None:
        await self.client.delete(key)

    async def close(self) -> None:
        await self.client.aclose()


_cache: MemoryCache | RedisCache | None = None
# 启动期 init_cache() 失败的原因。不记这个标记的话，Redis ping 失败会让 _cache
# 停在 None，随后任意 get_cache() 又惰性造一个 MemoryCache —— P1-9 的「连不上
# 就明确失败」被静默降级架空，多 worker 下配置/限流还会在进程间漂移。
_cache_init_error: str | None = None


def get_cache() -> MemoryCache | RedisCache:
    global _cache
    if _cache is None:
        if _cache_init_error:
            raise RuntimeError(f"缓存初始化失败，拒绝静默降级为内存缓存：{_cache_init_error}")
        # 仅测试 / 裸 ASGI（不跑 lifespan）场景允许惰性内存缓存；正式启动必经 init_cache()
        _cache = MemoryCache()
    return _cache


async def init_cache() -> None:
    """初始化缓存后端，并在启动期严格校验配置（P1-9）。

    - 多 worker（APP_WORKERS>1）必须使用 Redis：多进程共享数据库但各自持有内存
      缓存，配置/字典/限流会在 worker 间不一致，因此直接拒绝降级为内存缓存；
    - ``redis_enabled`` 时要求 ``redis_url`` 合法且连通（连不上立即失败，不静默降级）；
    - 单进程保留 MemoryCache。
    """
    global _cache, _cache_init_error
    _cache_init_error = None
    if settings.redis_enabled:
        url = (settings.redis_url or "").strip()
        if not url.startswith(("redis://", "rediss://")):
            _cache_init_error = "REDIS_URL 缺失或非法"
            raise RuntimeError(
                "REDIS_ENABLED=true 但 REDIS_URL 缺失或非法（需 redis:// 或 rediss://）"
            )
        cache = RedisCache(url)
        try:
            await cache.client.ping()
        except Exception as exc:
            # ping 失败时 client 已建好但没人接管：先关掉，别把连接池泄漏给 GC
            with contextlib.suppress(Exception):
                await cache.close()
            _cache_init_error = f"Redis 连接失败（{exc}）"
            raise
        _cache = cache
        logger.info("缓存后端=redis url=%s", url)
    else:
        if settings.app_workers > 1:
            raise RuntimeError(
                "APP_WORKERS>1 必须启用 Redis（REDIS_ENABLED=true）：多 worker 共享数据库但各自"
                "持有内存缓存，配置/字典/限流状态会在 worker 间不一致。"
            )
        _cache = MemoryCache()
        logger.info("缓存后端=memory（单进程，无跨 worker 一致性）")


async def close_cache() -> None:
    global _cache
    # 先把单例摘掉再 close：RedisCache.close() 抛异常时，_cache 不能继续指向
    # 一个半关闭的 client，否则后续 get_cache() 会拿到坏连接。
    cache, _cache = _cache, None
    # 不在这里清 _cache_init_error：启动失败 → lifespan 清理 → 进程内重启
    # 这条路径若把标记抹掉，get_cache() 又会静默降级成 MemoryCache。
    # 标记只由下一次成功的 init_cache() 重置。
    if cache is not None:
        await cache.close()
