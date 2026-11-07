"""兼容 shim：IP 守卫已上移到 ``app/kernel/ipguard.py``。

**为什么搬**：规则表 ``sys_ip_rule`` 是 ORM 模型（``IpRule``），守卫要按快照缓存读它，
属于有状态的底座。``normalize_*`` 三个纯函数也被 ``sys/security`` 复用，
它们跟着模型一起走比留在 core 更内聚。

下一轮删除本文件，把导入路径统一改成 ``app.kernel.ipguard``。
"""

from __future__ import annotations

from app.kernel.ipguard import (
    HIT_COUNT_PREFIX,
    HIT_TTL,
    IP_BLOCKED_CODE,
    MANAGEMENT_SUFFIXES,
    SNAPSHOT_KEY,
    SNAPSHOT_TTL,
    CompiledRule,
    IpGuardMiddleware,
    display_target,
    hit_stats,
    invalidate_snapshot,
    is_management_path,
    load_snapshot,
    match_rule,
    normalize_kind,
    normalize_scope,
    normalize_target,
    record_hit,
    request_scope,
)

__all__ = [
    "HIT_COUNT_PREFIX",
    "HIT_TTL",
    "IP_BLOCKED_CODE",
    "MANAGEMENT_SUFFIXES",
    "SNAPSHOT_KEY",
    "SNAPSHOT_TTL",
    "CompiledRule",
    "IpGuardMiddleware",
    "display_target",
    "hit_stats",
    "invalidate_snapshot",
    "is_management_path",
    "load_snapshot",
    "match_rule",
    "normalize_kind",
    "normalize_scope",
    "normalize_target",
    "record_hit",
    "request_scope",
]
