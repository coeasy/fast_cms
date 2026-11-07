"""静态资源版本化与缓存策略。

背景：页面由后端直接渲染，CSS/JS 用 ``<link>/<script>`` 引用。升级后如果 URL 不变，
浏览器会继续用旧缓存，出现「样式没变/脚本报错」这类只有刷新才暴露的问题。

方案：
1. 模板引用统一加 ``?v={{ asset_v() }}``，``asset_v`` 由「app_version + 静态目录内容指纹」
   得到。改任意一个 css/js 文件，指纹变化，URL 就变化，浏览器必定重新拉取。
2. 带 ``v=`` 的请求（入口资源）返回 ``Cache-Control: public, max-age=31536000, immutable``，
   一年内不再发请求——升级后 URL 变了，自然拉新文件。
3. 不带 ``v=`` 的请求（JS 内部相对 import，如 ``./core.js``，无法带查询串）返回
   ``no-cache``：浏览器每次都要带 ETag/Last-Modified 协商，服务端未变则 304。
   动态子模块因此永远新鲜，代价只是一次轻量协商。

``asset_v`` 每渲染一次调用一次，但指纹每 1 秒最多扫一次磁盘；稳态零磁盘 I/O，
改动后的版本在 1 秒内生效（满足「改一个 css 后刷新 URL 变化」的验收）。
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"

_VERSION_TTL = 1.0


@dataclass
class _CacheState:
    """进程级缓存：value 为当前版本号，ts 为上次计算时刻（monotonic 秒）。"""

    value: str = ""
    ts: float = 0.0


_CACHE = _CacheState()


def _fingerprint() -> str:
    """静态目录内容指纹：所有文件的相对路径 + mtime_ns + size。"""
    try:
        entries: list[str] = []
        for path in sorted(STATIC_DIR.rglob("*")):
            if path.is_file():
                stat = path.stat()
                entries.append(f"{path.relative_to(STATIC_DIR)}:{stat.st_mtime_ns}:{stat.st_size}")
        return "|".join(entries)
    except OSError:
        return ""


def asset_version() -> str:
    """短哈希版本号，任何静态文件变动都会改变它。

    每渲染一次调用一次，但指纹最多每 1 秒扫一次磁盘：稳态零磁盘 I/O，
    改动后的新版本在 1 秒内生效。
    """
    if time.monotonic() - _CACHE.ts > _VERSION_TTL:
        from app.core.config import settings  # 延迟导入避免循环依赖

        _CACHE.value = hashlib.sha1(f"{settings.app_version}|{_fingerprint()}".encode()).hexdigest()[:10]
        _CACHE.ts = time.monotonic()
    return _CACHE.value


def reset_cache() -> None:
    """清空版本号缓存，下次调用重新扫描磁盘。仅供测试使用。"""
    _CACHE.value = ""
    _CACHE.ts = 0.0


class VersionedStaticFiles(StaticFiles):
    """按是否带 ``v=`` 查询串下发不同的缓存策略。

    子类化而不是加中间件，避免对每个请求都走一遍自定义 ASGI 分发；
    只重写 ``get_response``，命中/未命中的缓存头都能覆盖到。
    """

    async def get_response(self, path: str, scope: dict) -> object:  # type: ignore[override]
        response = await super().get_response(path, scope)
        query = scope.get("query_string", b"").decode("latin-1")
        if re.search(r"(^|&)v=[^&]+", query):
            response.headers["Cache-Control"] = IMMUTABLE_CACHE
        else:
            response.headers["Cache-Control"] = NO_CACHE
        return response
