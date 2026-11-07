"""进程内事件总线 + 可追溯投递 + 事务性发件箱。

解决的问题：业务模块之间有几处「事后通知」型的依赖，写起来像调用，实际上
是单向广播——公告发布后要推送、配置改了要让调度器重排、任务失败要告警。
如果直接 import 订阅方，调用方就被迫认识一个与它业务无关的模块，模块依赖图
里多出一条没有业务含义的边；写在函数体里延迟导入更糟：它遮蔽的是循环依赖，
要等运行到那一行才炸。

于是改成：**发布方只声明「发生了什么」，订阅方在自己的 ``module.py`` 里注册**。

    # 发布方（content）
    from app.core import events
    await events.publish("notice.published", notice_id=1, title="...")

    # 订阅方（notify/module.py）
    from app.core import events
    from app.modules.ops.notify import hooks
    events.subscribe("notice.published", hooks.notice_published)

设计约束：

- **单进程内**：``publish()`` 在当前进程同步派发，没有跨进程投递，多 worker 部署时
  每个进程各自持有订阅表。定时任务的「只执行一次」由数据库锁保证（见 schedule/locks.py），
  与事件无关。
- **异常隔离**：单个订阅者抛异常只记录日志，不影响其它订阅者，也不回滚
  发布方已经提交的事务。这与原来的 ``_safe_emit`` 行为一致。
- **顺序**：按注册顺序串行 await，保持可预测；订阅方内部如需并发请自行处理。
- **可追溯（A-4 ①）**：``publish()`` 为每个订阅者落一条 ``EventLog``（事件名、订阅者、
  成败、耗时），把「通知没发出去」从一行日志变成可查询的审计轨迹。写入是尽力而为：
  数据库不可用不影响发布方。
- **事务性发件箱（A-4 ②）**：``enqueue_event(db, ...)`` 在同一事务写 ``EventOutbox``，
  ``drain_event_outbox(db, ...)`` 由 ``jobs/runner`` 轮询派发，获得至少一次投递 +
  跨进程一致 + 请求路径非阻塞。**``publish()`` 仍保持同步派发**（订阅方未做幂等，
  切到纯 outbox 会重复投递），但当**所有订阅者都失败**时，``publish()`` 会把该事件
  写入发件箱交给 drain 限次重试——这就是 outbox 的真实生产端。此前 ``enqueue_event``
  只被测试调用，默认启用的 drain 任务与 ``/metrics`` 积压 gauge 永远对着空表空转，
  是一条有消费端、无生产端的孤儿链路。
- 本模块属于 ``app/kernel``：它直接读 ``EventLog``/``EventOutbox`` 表，属于底座里「有状态」的那一层。``app/core`` 里留了同名 shim，调用点无需改动。
- **不得** import ``app.modules``；订阅关系由模块自己建立。
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.kernel.models import EventLog, EventOutbox
from app.kernel.models.events import OUTBOX_DONE, OUTBOX_FAILED, OUTBOX_PENDING

logger = logging.getLogger("fastcms.events")

Handler = Callable[..., Awaitable[None]]

# 已知事件清单：写在这里是为了让「发了一个没人订阅的事件」在门禁层可查，
# 而不是等到线上发现通知没发出去。新增事件必须同时补订阅方。
KNOWN_EVENTS: frozenset[str] = frozenset(
    {
        "notice.published",  # content → notify
        "job.finished",  # jobs.runner → notify
        "config.timezone_changed",  # config → schedule
    }
)

_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event: str, handler: Handler) -> None:
    """注册订阅。重复注册同一个函数是幂等的（模块被重复导入时不会重复推送）。"""
    if event not in KNOWN_EVENTS:
        raise ValueError(f"未登记的事件：{event!r}，请先补进 KNOWN_EVENTS 与订阅方")
    handlers = _handlers[event]
    if handler not in handlers:
        handlers.append(handler)


def _subscriber_name(handler: Handler) -> str:
    return getattr(handler, "__qualname__", repr(handler))


def _payload_summary(payload: dict[str, Any]) -> str:
    """payload 的轻量摘要（截断到 255）。非可序列化值兜底为字符串。"""
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        text = repr(payload)
    return text[:255]


def _payload_json(payload: dict[str, Any]) -> str:
    """payload 的完整 JSON（outbox 持久化用），非可序列化值兜底为字符串。"""
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return repr(payload)


async def _dispatch(event: str, payload: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """同步派发给所有订阅者，返回 ``(成功数, 每个订阅者的投递结果)``。

    单个订阅者异常被吞掉并记录，不影响其它订阅者（异常隔离）。
    """
    results: list[dict[str, Any]] = []
    handled = 0
    for handler in list(_handlers.get(event, ())):
        name = _subscriber_name(handler)
        started = time.perf_counter()
        try:
            await handler(**payload)
            handled += 1
            results.append(
                {"subscriber": name, "success": True, "duration_ms": int((time.perf_counter() - started) * 1000)}
            )
        except Exception:
            results.append(
                {
                    "subscriber": name,
                    "success": False,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "error": traceback.format_exc(limit=3)[:2000],
                }
            )
            logger.exception(
                "事件处理失败 event=%s handler=%s",
                event,
                name,
            )
    return handled, results


async def _write_event_logs_best_effort(
    event: str, payload: dict[str, Any], results: list[dict[str, Any]]
) -> None:
    """尽力而为地落 ``EventLog`` 投递记录；数据库不可用不影响发布方。

    使用独立 ``session_scope``（调用方不一定有会话），任何异常都吞掉。
    """
    try:
        from app.db.session import session_scope

        summary = _payload_summary(payload)
        async with session_scope() as db:
            for item in results:
                db.add(
                    EventLog(
                        event=event,
                        subscriber=item["subscriber"],
                        success=item["success"],
                        duration_ms=item["duration_ms"],
                        error=item.get("error"),
                        payload_summary=summary,
                    )
                )
    except Exception:
        # 这张表是「通知到底发出去没有」的唯一凭据，写失败必须能在日志里看到；
        # 用 DEBUG 等于默认不记，事后只能看到「消息没到」而查不到原因。
        logger.warning("写入事件投递日志失败 event=%s", event, exc_info=True)


async def publish(event: str, **payload: Any) -> int:
    """广播事件，返回成功处理的订阅者数量。

    订阅者异常被吞掉并记日志：通知推送失败不该让公告发布接口 500。
    未登记的事件同样只记日志不抛异常——发布方多在请求路径上，为一个拼错的名字
    让整个请求 500 不划算；真正的兜底是 ``scripts/check_modules.py`` 的静态检查，
    它在门禁层把「发布了一个没人登记的事件」直接判失败。

    同时为每个订阅者落一条 ``EventLog`` 投递记录（尽力而为，A-4 ①）。
    """
    if event not in KNOWN_EVENTS:
        logger.error("发布了未登记的事件 event=%s（已丢弃，请补进 KNOWN_EVENTS）", event)
        return 0
    handlers = list(_handlers.get(event, ()))
    if not handlers:
        logger.debug("事件没有订阅者 event=%s", event)
        return 0
    handled, results = await _dispatch(event, dict(payload))
    await _write_event_logs_best_effort(event, dict(payload), results)
    if handled == 0:
        # 全部订阅者失败：同步广播已经没有第二次机会了，持久化到发件箱让
        # drain 任务带退避重试（最多 5 次），避免「通知推不出去就彻底消失」。
        await _enqueue_failed_event(event, dict(payload))
    elif handled < len(results):
        # 部分失败不能进发件箱：重放会把已成功的订阅者再调一次（重复通知）。
        # 但也不能静默——逐订阅者的成败已写进 sys_event_log，这里明确留痕，
        # 提醒按业务补偿，而不是让人误以为整条链路都成功了。
        logger.warning(
            "事件 %s 部分订阅者失败（%d/%d 成功）：已成功的订阅者不重放，"
            "失败明细见 sys_event_log，需要时按业务补偿",
            event,
            handled,
            len(results),
        )
    return handled


async def _enqueue_failed_event(event: str, payload: dict[str, Any]) -> None:
    """把「零成功投递」的事件写进事务性发件箱；数据库不可用只记日志。

    与 ``_write_event_logs_best_effort`` 同样用独立 ``session_scope``：
    发布方不一定持有会话，且数据库故障绝不能让业务请求 500。

    测试环境（``APP_ENV=test``）不落库：事件总线的单测不依赖 ``client`` 夹具，
    直接写会污染会话级模板库，让后续「表里恰好 1 行」的断言随用例顺序漂移。
    这与审计队列在测试环境保持同步直写是同一条约定。
    """
    if settings.app_env.strip().lower() == "test":
        return
    try:
        from app.db.session import session_scope

        async with session_scope() as db:
            await enqueue_event(db, event, **payload)
    except Exception:
        logger.warning("事件 %s 全部订阅者失败且发件箱写入失败，投递放弃", event, exc_info=True)


async def enqueue_event(db: AsyncSession, event: str, **payload: Any) -> int:
    """在**调用方事务**里写一条 outbox 条目（A-4 ② 的事务性发件箱）。

    与 ``publish()`` 不同：这条记录不立即派发，而是由 ``drain_event_outbox()``
    在 ``jobs/runner`` 轮询里统一派发，从而获得至少一次投递、跨进程一致与
    请求路径非阻塞。调用方负责 ``commit()``（与业务写同一事务）。

    返回新条目 id。
    """
    if event not in KNOWN_EVENTS:
        raise ValueError(f"未登记的事件：{event!r}，请先补进 KNOWN_EVENTS")
    entry = EventOutbox(event=event, payload_json=_payload_json(dict(payload)))
    db.add(entry)
    await db.flush()
    return int(entry.id)


async def drain_event_outbox(db: AsyncSession, limit: int = 100) -> int:
    """派发 ``status="pending"`` 的 outbox 条目，返回成功派发条数。

    供 ``jobs/runner`` 轮询调用（与任务执行共用数据库会话）。每条派发后标记
    ``done`` 或 ``failed``（重试超限前保留 pending 供下次再试），并写 ``EventLog``。
    调用方负责 ``commit()``。
    """
    result = await db.execute(
        select(EventOutbox)
        .where(EventOutbox.status == OUTBOX_PENDING)
        .order_by(EventOutbox.id)
        .limit(limit)
    )
    entries = result.scalars().all()
    processed = 0
    for entry in entries:
        try:
            payload = json.loads(entry.payload_json)
        except Exception:
            payload = {}
        handled, _results = await _dispatch(entry.event, payload)
        entry.attempts += 1
        if handled > 0:
            entry.status = OUTBOX_DONE
            entry.processed_at = _now_utc()
        else:
            entry.last_error = f"无订阅者成功处理（尝试 {entry.attempts} 次）"
            # 重试上限：超过则置 failed，避免无限轮询同一坏条目
            if entry.attempts >= 5:
                entry.status = OUTBOX_FAILED
        processed += handled
        # 与 publish() 同口径：outbox 派发也写投递记录
        db.add(
            EventLog(
                event=entry.event,
                subscriber=f"outbox#{entry.id}",
                success=handled > 0,
                duration_ms=0,
                error=entry.last_error,
                payload_summary=entry.payload_json,
            )
        )
    return processed


def _now_utc() -> datetime:
    from app.db.base import utcnow

    return utcnow()


def subscribers(event: str) -> tuple[Handler, ...]:
    """返回某事件的订阅者，供测试与门禁断言「发布方有接收方」。"""
    return tuple(_handlers.get(event, ()))


def reset() -> None:
    """清空订阅表。仅测试使用。"""
    _handlers.clear()
