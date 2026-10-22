from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.core.config import settings


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


def get_cache() -> MemoryCache | RedisCache:
    global _cache
    if _cache is None:
        _cache = MemoryCache()
    return _cache


async def init_cache() -> None:
    global _cache
    if settings.redis_enabled:
        cache = RedisCache(settings.redis_url)
        await cache.client.ping()
        _cache = cache
    else:
        _cache = MemoryCache()


async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None
