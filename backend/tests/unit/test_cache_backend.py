"""P1-9：缓存后端启动期严格校验。

- 多 worker（APP_WORKERS>1）未开 Redis → 启动失败（不能静默降级为内存缓存）；
- redis_enabled=true 但 REDIS_URL 缺失/非法 → 启动失败；
- 单进程保留 MemoryCache。
"""

from __future__ import annotations

import pytest

from app.core.cache import MemoryCache, close_cache, get_cache, init_cache
from app.core.config import settings


async def test_multi_worker_requires_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_workers", 4)
    monkeypatch.setattr(settings, "redis_enabled", False)
    with pytest.raises(RuntimeError, match="REDIS"):
        await init_cache()
    await close_cache()


async def test_redis_requires_valid_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_enabled", True)
    monkeypatch.setattr(settings, "redis_url", "")
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        await init_cache()
    await close_cache()
    monkeypatch.setattr(settings, "redis_url", "http://not-redis")
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        await init_cache()
    await close_cache()


async def test_single_process_keeps_memory_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_workers", 1)
    monkeypatch.setattr(settings, "redis_enabled", False)
    await init_cache()
    try:
        assert isinstance(get_cache(), MemoryCache)
    finally:
        await close_cache()
