from __future__ import annotations

from datetime import datetime


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat(timespec="seconds") + "Z"
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
