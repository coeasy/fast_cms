from __future__ import annotations

import fakeredis.aioredis
import pytest
import redis.asyncio

import app.core.cache as cache_module
from app.core.cache import MemoryCache, RedisCache
from app.core.config import settings


@pytest.mark.asyncio
async def test_memory_cache_basic_flow():
    cache = MemoryCache()
    await cache.set("key", {"value": 1})
    assert await cache.get("key") == {"value": 1}
    assert await cache.incr("counter", ttl=60) == 1
    assert await cache.incr("counter", ttl=60) == 2

    assert await cache.acquire_lock("lock", ttl=60) is True
    assert await cache.acquire_lock("lock", ttl=60) is False
    await cache.release_lock("lock")
    assert await cache.acquire_lock("lock", ttl=60) is True

    await cache.delete("key")
    assert await cache.get("key") is None


@pytest.mark.asyncio
async def test_memory_cache_growth_is_bounded():
    cache = MemoryCache(max_items=64)
    for index in range(500):
        await cache.set(f"rate:{index}", 1, ttl=60)
    assert len(cache._store) <= 64
    # 淘汰后仍然可以正常读写，限流不会因为到达上限而报错
    await cache.set("fresh", 2, ttl=60)
    assert await cache.get("fresh") == 2


@pytest.mark.asyncio
async def test_redis_cache_uses_same_interface(monkeypatch):
    monkeypatch.setattr(
        redis.asyncio,
        "from_url",
        lambda *_args, **_kwargs: fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    cache = RedisCache("redis://127.0.0.1:6379/15")
    try:
        await cache.set("redis-key", {"value": "缓存"}, ttl=30)
        assert await cache.get("redis-key") == {"value": "缓存"}
        assert await cache.incr("redis-counter", ttl=30) == 1
        assert await cache.incr("redis-counter", ttl=30) == 2
        assert await cache.acquire_lock("redis-lock", ttl=30) is True
        assert await cache.acquire_lock("redis-lock", ttl=30) is False
        await cache.release_lock("redis-lock")
        assert await cache.acquire_lock("redis-lock", ttl=30) is True
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_init_cache_fails_fast_when_redis_enabled_but_unreachable(monkeypatch):
    class UnreachableRedis:
        async def ping(self):
            raise ConnectionError("redis://127.0.0.1:6399/0 连接被拒绝")

    monkeypatch.setattr(cache_module, "_cache", None)
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *_args, **_kwargs: UnreachableRedis())
    monkeypatch.setattr(settings, "redis_enabled", True)
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:6399/0")

    with pytest.raises(ConnectionError, match="6399"):
        await cache_module.init_cache()
    assert cache_module._cache is None
