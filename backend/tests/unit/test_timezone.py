from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.tz import (
    current_timezone,
    local_text,
    resolve_timezone,
    scheduler_timezone,
    set_timezone,
    to_system,
    zone,
)
from app.modules.config.service import TIMEZONE_CONFIG_KEY, _check_timezone

# 1 月不会被任何时区的夏令时干扰，断言偏移量才稳定
UTC_INSTANT = datetime(2026, 1, 9, 1, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def restore_runtime_timezone():
    previous = current_timezone()
    yield
    set_timezone(previous)


def test_resolve_timezone_accepts_iana_names():
    assert resolve_timezone("Asia/Shanghai") == "Asia/Shanghai"
    assert resolve_timezone(" utc ") == "UTC"


@pytest.mark.parametrize("bad", ["", "   ", "Beijing", "Asia/Nowhere", "GMT+9"])
def test_resolve_timezone_rejects_unusable_names(bad: str):
    with pytest.raises(ValueError):
        resolve_timezone(bad)


def test_local_text_follows_system_timezone():
    set_timezone("Asia/Shanghai")
    assert local_text(UTC_INSTANT) == "2026-01-09 09:30:00"
    set_timezone("America/New_York")
    assert local_text(UTC_INSTANT) == "2026-01-08 20:30:00"
    assert local_text(UTC_INSTANT, "%H:%M%z") == "20:30-0500"
    assert local_text(None) == ""


def test_to_system_returns_aware_datetime_in_current_zone():
    set_timezone("Europe/Paris")
    converted = to_system(UTC_INSTANT.replace(tzinfo=None))
    assert converted is not None and converted.utcoffset() == UTC_INSTANT.astimezone(ZoneInfo("Europe/Paris")).utcoffset()
    assert to_system(None) is None


def test_zone_defaults_to_runtime_and_scheduler_follows_system():
    set_timezone("UTC")
    assert zone() == ZoneInfo("UTC")
    assert zone("Asia/Tokyo") == ZoneInfo("Asia/Tokyo")
    assert scheduler_timezone() == "UTC"


def test_config_timezone_validator_only_guards_the_system_zone_key():
    """只有 system.timezone 参与时区校验，其它参数的值可以是任意字符串。"""
    _check_timezone("system.brand", "Beijing")
    _check_timezone(TIMEZONE_CONFIG_KEY, "Asia/Shanghai")
    with pytest.raises(AppError) as caught:
        _check_timezone(TIMEZONE_CONFIG_KEY, "Beijing")
    assert caught.value.code == "CONFIG_TIMEZONE_INVALID"
    assert caught.value.status_code == 422


def test_settings_rejects_bad_timezone_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("SCHEDULER_TIMEZONE", "Not/AZone")
    with pytest.raises(ValidationError):
        Settings()


def test_scheduler_timezone_follows_system_unless_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCHEDULER_TIMEZONE", raising=False)
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Berlin")
    settings = Settings()
    assert settings.app_timezone == "Europe/Berlin"
    assert settings.scheduler_timezone == ""
    assert scheduler_timezone() == current_timezone()
    monkeypatch.setenv("SCHEDULER_TIMEZONE", "Asia/Tokyo")
    assert Settings().scheduler_timezone == "Asia/Tokyo"
