from __future__ import annotations

import contextlib
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

# 异常分级规范（P2-3）：任何异常处理都必须带上下文（context），
# 并区分「可降级 / 不可降级」：
#   - 可降级（可选副作用失败，不应拖垮主流程）→ log_exception(level=WARNING, context=...)，
#     然后走降级路径继续；
#   - 不可降级（主流程依赖，失败即中断）→ log_exception(level=ERROR, context=...)，再上抛，
#     由全局 handler 兜底返回 500，request_id 让故障可定位。
# 禁止出现「裸 except 且无任何日志/上下文」的吞错。


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


def log_exception(
    logger: logging.Logger,
    exc: BaseException | None = None,
    *,
    context: dict[str, Any] | None = None,
    level: int = logging.WARNING,
) -> None:
    """按异常分级规范记录一次异常（含上下文与当前 request_id）。

    ``context`` 会随结构化日志一起输出，配合 JsonFormatter 里的 request_id，
    故障注入/线上排查都能从日志定位到具体请求与业务上下文。
    可降级失败传 ``level=WARNING`` 并继续降级路径；不可降级失败传 ``level=ERROR``
    并在本函数返回后重新上抛。
    """
    payload = {k: v for k, v in (context or {}).items() if v is not None}
    if exc is not None:
        logger.log(level, "异常发生（按 request_id 可定位）", exc_info=exc, extra={"context": payload})
    else:
        logger.log(level, "异常发生（按 request_id 可定位）", extra={"context": payload})


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
        context = getattr(record, "context", None)
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8")
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
