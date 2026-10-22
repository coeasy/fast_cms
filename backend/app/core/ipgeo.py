"""离线 IP 归属地查询（ip2region xdb 二进制库）。

设计要点：
- 数据随包分发：``backend/app/ip_data/ip2region_v4.xdb``、``ip2region_v6.xdb``，
  查询全程不访问外网，也没有速率限制。
- 三种缓存策略（``IP2REGION_CACHE_POLICY``）：``file`` 每次查询读文件（最省内存）、
  ``vectorIndex`` 只缓存向量索引（默认，单次查询约 0.2ms）、``content`` 全量进内存
  （v4+v6 约 46MB，零 IO）。官方实现里 ``file``/``vectorIndex`` 的 Searcher
  **不是线程安全**的，``content`` 的才是。
- 因此查询一律经 ``asyncio.to_thread`` 执行：既不会用文件 IO 卡住事件循环，
  也顺理成章地按线程隔离 Searcher（``content`` 策略全局共享一个实例）。
- 库文件缺失或校验失败只告警不抛异常：归属地是增强信息，不该阻断启动与登录。
  此时 ``locate()`` 返回 ``None``，界面显示「未知」。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import ip2region.searcher as xdb
import ip2region.util as util

from app.core.config import settings

logger = logging.getLogger("ipgeo")

# IP → 归属地是准静态数据（只有换 xdb 才会变），而登录日志、在线用户列表会反复
# 命中同一批 IP。加一层有界缓存后重复查询零成本，也不用担心无限增长。
CACHE_SIZE = 4096

# region 串字段顺序：国家|省份|城市|ISP|国家码（当前 xdb 数据实测，13 个样本核对）。
# 注意 ip2region 2.x 的老库是 国家|区域|省份|城市|ISP——字段数一样但语义不同。
# 随包分发的就是新格式库；万一拿到老库，前两列会被对调，所以这里保留 raw 原串，
# 排障时按原串判断，不对未知格式猜字段。
REGION_FIELDS = ("country", "province", "city", "isp", "country_code")

# 展示用简称：江苏省 → 江苏。顺序不能换，「维吾尔自治区」要先于「自治区」匹配。
_COMPACT_SUFFIXES = ("维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区", "自治区", "省", "市")


def _compact(name: str | None) -> str | None:
    """去掉行政区划后缀，让表格列里的「江苏 南京」比「江苏省 南京市」短一截。"""
    if not name:
        return None
    for suffix in _COMPACT_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _clean(value: str) -> str | None:
    """xdb 用 "0" 表示「无此级数据」，归一成 None，别把 0 当成地名展示。"""
    text = value.strip()
    return text if text and text != "0" else None


@dataclass(frozen=True, slots=True)
class IpLocation:
    """一次查询的结构化结果。``raw`` 是 xdb 原始串，排障时看它就不会被解析误导。"""

    country: str | None = None
    province: str | None = None
    city: str | None = None
    isp: str | None = None
    country_code: str | None = None
    raw: str = ""

    @property
    def label(self) -> str:
        """完整归属地：``中国 江苏 南京 电信``；查不到时退回 raw（如「内网地址」）。"""
        parts = [part for part in (self.country, _compact(self.province), _compact(self.city), self.isp) if part]
        return " ".join(parts) if parts else (self.raw or "未知")

    @property
    def place(self) -> str:
        """短地名：``江苏 南京``；海外只有省/州时退化成 ``United States California``。"""
        parts = [part for part in (_compact(self.province), _compact(self.city)) if part]
        if not parts:
            parts = [part for part in (self.country,) if part]
        return " ".join(parts) if parts else (self.raw or "未知")

    @property
    def domestic(self) -> bool:
        return self.country_code == "CN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "province": self.province,
            "city": self.city,
            "isp": self.isp,
            "country_code": self.country_code,
            "label": self.label,
            "place": self.place,
            "domestic": self.domestic,
            "raw": self.raw,
        }


def _private_label(ip: str) -> str | None:
    """内网 / 回环 / 链路本地地址在 xdb 里查不到，给个人话标签。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_loopback:
        return "本机"
    if addr.is_private or addr.is_link_local or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return "内网地址"
    return None


def _parse(raw: str) -> IpLocation:
    parts = [_clean(part) for part in raw.split("|")]
    parts += [None] * (len(REGION_FIELDS) - len(parts))
    country, province, city, isp, code = parts[: len(REGION_FIELDS)]
    # 只有形如 CN/US 的两字母码才当国家码，避免把老格式的运营商名塞进这个字段
    country_code = code if code and len(code) == 2 and code.isalpha() and code.isupper() else None
    return IpLocation(
        country=country,
        province=province,
        city=city,
        isp=isp,
        country_code=country_code,
        raw=raw,
    )


class _State:
    """进程级查询状态：每个 IP 版本一份，字典 value 是给 describe() 用的描述信息。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ready = False
        self.entries: dict[str, dict[str, Any]] = {}


_STATE = _State()
_LOCAL = threading.local()


def _load() -> None:
    if not settings.ip2region_enabled:
        logger.info("IP2REGION_ENABLED=false，IP 归属地功能已关闭")
        return
    policy = settings.ip2region_cache_policy
    data_dir: Path = settings.ip2region_dir
    for version, tag in ((util.IPv4, "v4"), (util.IPv6, "v6")):
        name = version.name
        path = data_dir / f"ip2region_{tag}.xdb"
        entry: dict[str, Any] = {
            "version": name,
            "path": str(path),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "loaded": False,
        }
        if not path.is_file():
            # 缺文件不抛异常：归属地只是增强信息。但要把话说明白——
            # 静默退化会让「IP 归属地」列永远显示未知，还查不出原因。
            logger.warning("IP 归属地数据缺失：%s（%s 查询返回空），可放置 xdb 文件后重启", path, name)
            _STATE.entries[name] = entry
            continue
        try:
            # 启动时校验一次：绑定版本与 xdb 不匹配时明确失败，
            # 而不是等查询时报难以理解的错
            util.verify_from_file(str(path))
            if policy == "content":
                content = util.load_content_from_file(str(path))
                # content 查询器并发安全，全局建一个就够
                entry["searcher"] = xdb.new_with_buffer(version, content)
            elif policy == "vectorIndex":
                entry["vector_index"] = util.load_vector_index_from_file(str(path))
            entry["loaded"] = True
        except Exception:
            logger.exception("IP 归属地库加载失败：%s", path)
        _STATE.entries[name] = entry


def _ensure_ready() -> None:
    if _STATE.ready:
        return
    with _STATE.lock:
        if _STATE.ready:
            return
        _load()
        _STATE.ready = True


def _searcher(version: util.Version) -> Any:
    entry = _STATE.entries.get(version.name)
    if entry is None or not entry.get("loaded"):
        return None
    policy = settings.ip2region_cache_policy
    if policy == "content":
        return entry.get("searcher")
    # file / vectorIndex：Searcher 非线程安全，按线程各建一个（共享同一份索引缓存）
    cache: dict[str, Any] | None = getattr(_LOCAL, "searchers", None)
    if cache is None:
        cache = {}
        _LOCAL.searchers = cache
    name = version.name
    searcher = cache.get(name)
    if searcher is None:
        path = entry["path"]
        if policy == "vectorIndex":
            searcher = xdb.new_with_vector_index(version, path, entry["vector_index"])
        else:
            searcher = xdb.new_with_file_only(version, path)
        cache[name] = searcher
    return searcher


def _lookup(ip: str) -> IpLocation | None:
    _ensure_ready()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    version = util.IPv4 if addr.version == 4 else util.IPv6
    if not _STATE.entries.get(version.name, {}).get("loaded"):
        # 库没加载时不进缓存：否则进程内永远缓存「查不到」，热更新文件也无从生效
        return None
    return _search(ip, version.name)


@lru_cache(maxsize=CACHE_SIZE)
def _search(ip: str, version_name: str) -> IpLocation | None:
    version = util.IPv4 if version_name == "IPv4" else util.IPv6
    searcher = _searcher(version)
    if searcher is None:
        return None
    try:
        raw = searcher.search(ip)
    except Exception:
        logger.exception("IP 归属地查询失败：%s", ip)
        return None
    return _parse(raw)


def locate_sync(ip: str | None) -> IpLocation | None:
    """同步查询。CLI / 脚本用；Web 请求一律走 :func:`locate`。"""
    if not ip:
        return None
    text = ip.strip()
    if not text:
        return None
    private = _private_label(text)
    if private is not None:
        return IpLocation(raw=private)
    return _lookup(text)


async def locate(ip: str | None) -> IpLocation | None:
    """异步查询单个 IP。空 / 非法 / 库缺失都返回 ``None``，不抛异常。"""
    if not ip:
        return None
    text = ip.strip()
    if not text:
        return None
    # 丢线程池：vectorIndex/file 策略每次查询有文件 IO，且 Searcher 非线程安全，
    # 直接调用会卡住事件循环。缓存命中时也只是多一次线程切换，可忽略。
    return await asyncio.to_thread(locate_sync, text)


async def locate_many(ips: Iterable[str | None]) -> dict[str, IpLocation | None]:
    """批量查询：去重后并发执行，供列表页避免逐个串行等待。

    返回 ``{ip: location}``，调用方按原始 IP 取值；查不到的 IP 值为 ``None``。
    """
    unique = [text for text in {ip.strip() for ip in ips if ip and ip.strip()}]
    if not unique:
        return {}
    found = await asyncio.gather(*(locate(ip) for ip in unique))
    return dict(zip(unique, found, strict=True))


def label_of(locations: Mapping[str, IpLocation | None] | None, ip: str | None) -> str | None:
    """从 :func:`locate_many` 的结果里取展示用归属地文本。"""
    if not locations or not ip:
        return None
    location = locations.get(ip.strip())
    return location.label if location is not None else None


async def startup() -> None:
    """lifespan 调用：提前加载并校验 xdb，把问题暴露在启动阶段而不是首次查询时。"""
    await asyncio.to_thread(_ensure_ready)
    entries = _STATE.entries
    if not entries:
        logger.info("IP 归属地未启用")
        return
    loaded = [f"{item['version']}({item['size_bytes'] // 1024}KB)" for item in entries.values() if item["loaded"]]
    missing = [item["version"] for item in entries.values() if not item["loaded"]]
    logger.info(
        "IP 归属地就绪 策略=%s 已加载=%s 未加载=%s",
        settings.ip2region_cache_policy,
        "/".join(loaded) or "无",
        "/".join(missing) or "无",
    )


def describe() -> dict[str, Any]:
    """查询界面与排障用：策略、数据文件与加载状态。"""
    _ensure_ready()
    return {
        "enabled": settings.ip2region_enabled,
        "policy": settings.ip2region_cache_policy,
        "data_dir": str(settings.ip2region_dir),
        "versions": list(_STATE.entries.values()),
    }


def _reset() -> None:
    """测试用：清空状态与缓存，模拟「未加载 / 已加载」两种起点。"""
    with _STATE.lock:
        _STATE.ready = False
        _STATE.entries = {}
    _search.cache_clear()
    _LOCAL.searchers = {}
