from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Any

from app.core.errors import get_request_id
from app.core.tz import zone

SENSITIVE_KEYS = {
    "password",
    "old_password",
    "new_password",
    "password_hash",
    "refresh_token",
    "access_token",
    "token",
    "secret",
    # 通知渠道的 webhook 地址自带 access_token，审计详情里同样不能留原文。
    "webhook_url",
    "cookie",
    "authorization",
}


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                result[key] = "***"
            else:
                result[key] = sanitize(item)
        return result
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def truncate_json(value: Any, limit: int = 4000) -> str:
    text = json.dumps(sanitize(value), ensure_ascii=False, default=str)
    return text[:limit]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # 日志时间戳跟随系统时区，方便和后台页面、任务记录对时间
            "timestamp": datetime.fromtimestamp(record.created, tz=zone()).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
