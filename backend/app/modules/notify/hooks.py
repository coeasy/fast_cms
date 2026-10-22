from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.core.tz import local_text
from app.db.base import utcnow
from app.modules.notify import service

logger = logging.getLogger("fastcms.notify")

JOB_EVENTS = {"failed": "job.failed", "interrupted": "job.interrupted"}
ERROR_LIMIT = 800


def _fmt(value: datetime | None) -> str:
    if value is None:
        return "未知"
    # 推送正文给人看，必须按系统时区而不是服务器所在时区
    return local_text(value)


async def _safe_emit(event: str, dedupe_key: str, title: str, content: str) -> None:
    try:
        await service.emit(event=event, dedupe_key=dedupe_key, title=title, content=content)
    except Exception:
        # 推送失败不能影响任务收尾或公告发布，记录里已有失败线索。
        logger.exception("通知推送排队失败：event=%s dedupe_key=%s", event, dedupe_key)


async def job_finished(
    *,
    log_id: int | None,
    job: dict[str, Any],
    status: str,
    error: str | None,
    duration_ms: int,
    trigger: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    event = JOB_EVENTS.get(status)
    if event is None or log_id is None:
        return
    label = "执行失败" if status == "failed" else "执行被中断"
    await _safe_emit(
        event,
        f"job_log:{log_id}",
        f"定时任务{label}：{job['name']}",
        "\n".join(
            [
                f"- 任务编码：{job['code']}",
                f"- 任务类型：{job.get('job_type') or 'registry'}",
                f"- 触发方式：{trigger}",
                f"- 开始时间：{_fmt(started_at)}",
                f"- 结束时间：{_fmt(finished_at or utcnow())}",
                f"- 耗时：{duration_ms} ms",
                f"- 错误信息：{(error or '无')[:ERROR_LIMIT]}",
            ]
        ),
    )


async def notice_published(
    *,
    notice_id: int,
    title: str,
    content: str,
    published_at: datetime | None,
    publisher: str | None,
) -> None:
    snippet = " ".join((content or "").split())[:500] or "（无正文）"
    await _safe_emit(
        "notice.published",
        f"notice:{notice_id}",
        f"新公告：{title}",
        "\n".join(
            [
                f"- 公告标题：{title}",
                f"- 发布时间：{_fmt(published_at or utcnow())}",
                f"- 发布人：{publisher or '系统'}",
                f"- 内容摘要：{snippet}",
            ]
        ),
    )
