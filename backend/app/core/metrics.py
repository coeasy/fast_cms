"""进程内指标（B-8 / 4-2）：QPS、延迟、连接池可采集。

无外部依赖、无第三方客户端：Prometheus 文本格式手工渲染，``/metrics`` 端点
（``METRICS_ENABLED=true`` 时注册）返回它。指标是进程内的近似值——多 worker
部署时每个 worker 各自持有计数，``/metrics`` 只能看到本进程的视角；生产聚合
交给 Prometheus 抓取（``scrape_configs`` 指向每个实例的 ``/metrics`` 即可）。

线程安全：``asyncio`` 单事件循环下计数都在请求路径串行执行，加一把 ``Lock``
兜底并发的多线程访问（如后台任务）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("fastcms.metrics")

# 延迟分桶（秒）：与常见 SLO 对齐，供直方图近似分位数。
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
# 状态码聚合桶：2xx / 3xx / 4xx / 5xx
_STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx")
# 由上面这份清单派生，避免「清单说四个桶、判断里又手写一遍」的两处真相
_STATUS_CLASS_CODES = frozenset(int(name[0]) for name in _STATUS_CLASSES)

_lock = threading.Lock()
_requests_total: dict[tuple[str, str], int] = defaultdict(int)  # (method, status_class) -> count
_duration_sum = 0.0
_duration_count = 0
_latency_buckets_count: dict[float, int] = defaultdict(int)  # bucket upper bound -> count
_started_at = time.monotonic()
# 已告警过的 gauge：同一个坏 gauge 每次抓取都打日志会把日志刷满
_gauge_failures: set[str] = set()


def _status_class(status: int) -> str:
    code = status // 100
    return f"{code}xx" if code in _STATUS_CLASS_CODES else "5xx"


def record_request(method: str, status: int, duration_ms: float) -> None:
    """请求完成时由中间件调用：累计计数、时长与延迟分桶。"""
    with _lock:
        global _duration_sum, _duration_count
        cls = _status_class(status)
        _requests_total[(method.upper(), cls)] += 1
        seconds = max(duration_ms, 0.0) / 1000.0
        _duration_sum += seconds
        _duration_count += 1
        for bound in _LATENCY_BUCKETS:
            if seconds <= bound:
                _latency_buckets_count[bound] += 1


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# ---- Gauge 注册表（Phase 11-4）--------------------------------------------
#
# counter / histogram 由请求路径主动累加；gauge 则相反——值分散在各自的所有者
# 手里（连接池在 engine、审计队列在 audit 模块），所以反过来用「注册回调」：
# 渲染时实时取值，谁的状态谁自己最清楚，metrics 模块不做任何缓存。
# 还有一部分 gauge 取值需要查库（如 outbox 积压），走 ``render(extra)`` 由
# /metrics 端点（本身是 async）先查好再传入。
_gauges: dict[str, tuple[str, Callable[[], float]]] = {}


def register_gauge(name: str, help_text: str, fn: Callable[[], float]) -> None:
    """注册一个实时 gauge：``fn()`` 在每次渲染时调用，抛异常时按缺失处理。"""
    with _lock:
        _gauges[name] = (help_text, fn)


def _render_metrics(extra: dict[str, float] | None = None) -> str:
    with _lock:
        # _started_at 取的是 time.monotonic()；混用 time.time() 会得到
        # 「epoch - 进程启动时刻」这种约 1.7e9 秒的恒定垃圾值。
        uptime = time.monotonic() - _started_at
        total = sum(_requests_total.values())
        count = _duration_count
        duration_sum = _duration_sum
        requests_snapshot = dict(_requests_total)
        latency_snapshot = dict(_latency_buckets_count)
        gauges_snapshot = dict(_gauges)

    lines: list[str] = [
        "# HELP fastcms_uptime_seconds Process uptime in seconds.",
        "# TYPE fastcms_uptime_seconds gauge",
        f"fastcms_uptime_seconds {uptime:.3f}",
        "# HELP fastcms_http_requests_total HTTP requests by method and status class.",
        "# TYPE fastcms_http_requests_total counter",
    ]
    for (method, cls), value in sorted(requests_snapshot.items()):
        lines.append(
            f'fastcms_http_requests_total{{method="{_escape_label(method)}",status="{cls}"}} {value}'
        )
    lines.append("# HELP fastcms_http_request_duration_seconds_sum Total request duration in seconds.")
    lines.append("# TYPE fastcms_http_request_duration_seconds_sum counter")
    lines.append(f"fastcms_http_request_duration_seconds_sum {duration_sum:.6f}")
    lines.append("# HELP fastcms_http_request_duration_seconds_count Total requests observed.")
    lines.append("# TYPE fastcms_http_request_duration_seconds_count counter")
    lines.append(f"fastcms_http_request_duration_seconds_count {count}")
    lines.append("# HELP fastcms_http_request_duration_seconds_bucket Request duration histogram.")
    lines.append("# TYPE fastcms_http_request_duration_seconds_bucket histogram")
    for bound in _LATENCY_BUCKETS:
        lines.append(
            f'fastcms_http_request_duration_seconds_bucket{{le="{bound}"}} '
            f"{latency_snapshot.get(bound, 0)}"
        )
    lines.append(f'fastcms_http_request_duration_seconds_bucket{{le="+Inf"}} {count}')
    lines.append("# HELP fastcms_http_requests_observed Total requests observed (alias of count).")
    lines.append("# TYPE fastcms_http_requests_observed counter")
    lines.append(f"fastcms_http_requests_observed {total}")

    for name in sorted(gauges_snapshot):
        help_text, fn = gauges_snapshot[name]
        try:
            gauge_value: float = fn()
        except Exception:
            # gauge 取值失败按缺失处理（不能让整个抓取 500），但必须留一次日志：
            # 否则一个永久坏掉的 gauge 会从 /metrics 里静默消失，告警永远不触发。
            if name not in _gauge_failures:
                _gauge_failures.add(name)
                logger.warning("gauge 取值失败，本次抓取跳过 %s", name, exc_info=True)
            continue
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {gauge_value:.0f}")

    for name in sorted(extra or {}):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {(extra or {})[name]:.0f}")

    return "\n".join(lines) + "\n"


def render(extra: dict[str, float] | None = None) -> str:
    """Prometheus 文本格式的指标快照（供 ``/metrics`` 端点）。

    ``extra`` 是端点侧查好的一次性 gauge（如 outbox 积压数——需要查库，
    不能注册成同步回调）。
    """
    return _render_metrics(extra)


def snapshot() -> dict[str, Any]:
    """测试用：返回当前计数快照。"""
    with _lock:
        return {
            "requests_total": dict(_requests_total),
            "duration_sum": _duration_sum,
            "duration_count": _duration_count,
            "latency": dict(_latency_buckets_count),
        }


def reset() -> None:
    """清空所有计数（仅测试使用）。"""
    with _lock:
        global _duration_sum, _duration_count, _started_at
        _requests_total.clear()
        _latency_buckets_count.clear()
        _duration_sum = 0.0
        _duration_count = 0
        _started_at = time.monotonic()
        _gauge_failures.clear()
