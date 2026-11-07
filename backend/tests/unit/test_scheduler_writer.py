"""A-9 / 3-6：``next_run_at`` 单一写者机制与嵌入式多 worker 策略的单元测试。

- ``api_owns_next_run()``：standalone 时 API 进程只读，embedded/disabled 拥有写权；
- ``embedded_multiworker_policy()``：embedded + 多 worker 默认拒绝，显式开启才放行。
"""

from __future__ import annotations

from app.core.config import settings
from app.modules.ops.schedule.scheduler import api_owns_next_run
from app.startup import embedded_multiworker_policy


def _patch_settings(**kwargs):
    originals = {}
    for key, value in kwargs.items():
        originals[key] = getattr(settings, key)
        setattr(settings, key, value)
    return originals


def _restore(originals):
    for key, value in originals.items():
        setattr(settings, key, value)


def test_api_owns_next_run_false_in_standalone() -> None:
    originals = _patch_settings(scheduler_mode="standalone")
    try:
        assert api_owns_next_run() is False, "standalone 时 API 进程必须只读 next_run_at"
    finally:
        _restore(originals)


def test_api_owns_next_run_true_in_embedded() -> None:
    originals = _patch_settings(scheduler_mode="embedded")
    try:
        assert api_owns_next_run() is True
    finally:
        _restore(originals)


def test_api_owns_next_run_true_in_disabled() -> None:
    originals = _patch_settings(scheduler_mode="disabled")
    try:
        assert api_owns_next_run() is True
    finally:
        _restore(originals)


def test_embedded_multiworker_raises_by_default() -> None:
    originals = _patch_settings(
        scheduler_mode="embedded", app_workers=2, scheduler_embedded_multiworker=False
    )
    try:
        assert embedded_multiworker_policy() == "raise", "未显式开启时必须拒绝"
    finally:
        _restore(originals)


def test_embedded_multiworker_allows_when_explicit() -> None:
    originals = _patch_settings(
        scheduler_mode="embedded", app_workers=2, scheduler_embedded_multiworker=True
    )
    try:
        assert embedded_multiworker_policy() == "allow", "显式开启应放行（带警告）"
    finally:
        _restore(originals)


def test_embedded_single_worker_is_ok() -> None:
    originals = _patch_settings(
        scheduler_mode="embedded", app_workers=1, scheduler_embedded_multiworker=False
    )
    try:
        assert embedded_multiworker_policy() == "ok"
    finally:
        _restore(originals)


def test_standalone_multiple_workers_is_ok() -> None:
    originals = _patch_settings(
        scheduler_mode="standalone", app_workers=4, scheduler_embedded_multiworker=False
    )
    try:
        assert embedded_multiworker_policy() == "ok", "standalone 本就是要独立进程，多 worker 合法"
    finally:
        _restore(originals)
