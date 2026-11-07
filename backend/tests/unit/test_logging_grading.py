"""异常分级规范测试（P2-3）。

验收点：
- 可降级/不可降级失败都通过 ``log_exception`` 记录，且日志携带业务 context；
- JsonFormatter 输出的结构化日志包含 request_id（故障可从日志定位）；
- 带异常对象时日志含 traceback；不传异常时无 exception 字段；
- 故障注入：捕获异常后记录，日志可解析出 context 与 request_id。
"""

from __future__ import annotations

import io
import json
import logging

from app.core.errors import request_id_var
from app.core.logging import JsonFormatter, log_exception


def _capture_logger() -> tuple[logging.Logger, io.StringIO]:
    logger = logging.getLogger("test.grading")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger, stream


def _records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().strip().splitlines() if line.strip()]


def test_log_exception_carries_context_and_request_id() -> None:
    token = request_id_var.set("req-grading-001")
    try:
        logger, stream = _capture_logger()
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            log_exception(logger, exc, context={"user_id": 7, "action": "export"}, level=logging.ERROR)
    finally:
        request_id_var.reset(token)
    records = _records(stream)
    assert len(records) == 1
    record = records[0]
    assert record["request_id"] == "req-grading-001"
    assert record["context"] == {"user_id": 7, "action": "export"}
    assert "Traceback" in record["exception"]
    assert record["level"] == "ERROR"


def test_log_exception_without_exc_has_no_traceback() -> None:
    logger, stream = _capture_logger()
    log_exception(logger, context={"note": "degraded"}, level=logging.WARNING)
    record = _records(stream)[0]
    assert record["context"] == {"note": "degraded"}
    assert "exception" not in record
    assert record["level"] == "WARNING"


def test_fault_injection_swallowed_failure_is_locateable() -> None:
    """模拟可降级失败：捕获后记录，日志必须能定位到具体业务上下文。"""
    logger, stream = _capture_logger()
    token = request_id_var.set("req-fault-042")
    try:
        try:
            raise ConnectionError("webhook 不可达")
        except ConnectionError as exc:
            log_exception(logger, exc, context={"channel": "webhook", "retry": 1})
    finally:
        request_id_var.reset(token)
    record = _records(stream)[0]
    assert record["request_id"] == "req-fault-042"
    assert record["context"]["channel"] == "webhook"
    assert "ConnectionError" in record["exception"]
