from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import DEFAULT_TIMEZONE, settings, timezone_key

# 系统时区是运行期状态：环境变量给出初值，管理员改 `system.timezone` 后立刻生效，
# 进程重启时由启动流程从数据库读回来，所以不能只读 settings 字段。
_runtime_key: str = settings.app_timezone


def resolve_timezone(name: str | None) -> str:
    """校验时区名并返回规范写法；空值同样拒绝，避免系统参数留空后悄悄换时区。"""
    raw = (name or "").strip()
    if not raw:
        raise ValueError(f"时区不能为空，例如 {DEFAULT_TIMEZONE}")
    return timezone_key(raw, "system.timezone")


def zone(name: str | None = None) -> ZoneInfo:
    """按名字取时区，缺省用当前系统时区。"""
    return ZoneInfo(name or _runtime_key)


def current_timezone() -> str:
    return _runtime_key


def scheduler_timezone() -> str:
    """调度默认时区：显式配置 SCHEDULER_TIMEZONE 优先，否则跟随系统时区。"""
    return settings.scheduler_timezone or _runtime_key


def set_timezone(name: str | None) -> str:
    """应用新的系统时区并返回生效的时区名。"""
    global _runtime_key
    _runtime_key = resolve_timezone(name)
    return _runtime_key


def to_system(value: datetime | None) -> datetime | None:
    """把 UTC（或裸 UTC）时刻换算到当前系统时区。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(zone())


def local_text(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """服务端面向人的时间输出（CSV 导出、通知正文）。"""
    local = to_system(value)
    return local.strftime(fmt) if local is not None else ""
