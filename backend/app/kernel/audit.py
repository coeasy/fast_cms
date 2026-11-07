"""操作日志的批量落库通道（B-7 / Phase 11-3）。

中间件兜底审计过去是「每个变更请求开一个独立事务写一条日志」：业务事务之外
多出一次完整的连接获取 + INSERT + COMMIT。在写入高峰，这部分开销与业务写入
同量级——请求延迟被审计拖累，正是 B-7 的病灶。

本模块把中间件路径的审计写改成**进程内队列**：

- ``enqueue()`` 由请求路径调用，纯内存操作，微秒级；
- 后台任务每 ``audit_flush_interval_seconds``（默认 200ms）把攒住的记录
  一次性批量 INSERT（单事务、单连接），摊薄固定开销；
- 队列满（``audit_queue_capacity``）或队列未启动（裸 ASGI / 测试环境）时，
  ``enqueue`` 返回 False，调用方**降级为同步直写**——宁可拖慢这一条请求，
  也不丢审计记录；
- 刷盘失败时记录塞回队头下一轮重试，并带指数退避；**连续失败达到
  ``audit_max_retries`` 轮后丢弃该批毒丸记录**并打 ERROR —— 否则一条坏数据会让
  后台任务每 200ms 重试同一批，形成永不终止的无效事务循环；
- 停机走「通知退出 + 等待当前批次提交」而不是直接 ``cancel()``：取消若命中
  ``await session.commit()``，已经出队的整批记录会永久丢失；``flush()`` 对
  ``CancelledError`` 也回队，双保险。之后 ``stop()`` 会带总时限把所有剩余
  批次排空（后台任务收到停机信号时只写完当前批），不会只写一批就丢剩下的。

只有中间件兜底写走队列；service 层的 ``log_operation`` 与业务同事务，
天然批量（随业务 commit 落库），不需要也不能改。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any

from app.core.config import settings

logger = logging.getLogger("fastcms.audit")


_STOP_WAIT_SECONDS = 5.0
# 停机排空的总时限：坏库不能让 lifespan 的 finally 永远卡住
_STOP_DRAIN_SECONDS = 10.0
_MAX_BACKOFF_SECONDS = 5.0


class AuditWriteQueue:
    def __init__(self) -> None:
        self._items: deque[dict[str, Any]] = deque()
        self._task: asyncio.Task[None] | None = None
        # 停机信号（不 cancel）：让正在提交的批次跑完再退出
        self._stop = asyncio.Event()
        # 最近一次出队的条数：连续失败需要精确丢弃「那一批」毒丸记录
        self._last_batch_size = 0

    # ---- 请求路径（非阻塞） -------------------------------------------

    def enqueue(self, row: dict[str, Any]) -> bool:
        """入队一条审计记录；返回 False 表示调用方应降级直写。

        两种情况返回 False：后台任务未启动（测试环境直接构造 app、不跑
        lifespan），或队列已满（持续写入速度超过落库速度，继续入队只会
        把内存吃穿——此时逐条直写是最安全的背压方式）。容量下限钳到 1，
        避免误配 ``AUDIT_QUEUE_CAPACITY=0`` 让队列永远进不去。
        """
        if self._task is None or len(self._items) >= max(1, settings.audit_queue_capacity):
            return False
        self._items.append(row)
        return True

    def depth(self) -> int:
        """当前积压条数（/metrics 的 ``fastcms_audit_queue_depth`` gauge）。"""
        return len(self._items)

    # ---- 生命周期 ------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="audit-writer")

    async def stop(self) -> None:
        task, self._task = self._task, None
        self._stop.set()
        if task is not None:
            # asyncio.wait 不会取消任务：正在 flush 的批次可以完整提交。
            # 只有真正卡死（DB 挂住）才在超时后强制取消，并如实记 ERROR。
            done, _ = await asyncio.wait({task}, timeout=_STOP_WAIT_SECONDS)
            if not done:
                logger.error(
                    "审计刷盘任务 %.0fs 内未退出，强制取消；队列尚有 %d 条待确认",
                    _STOP_WAIT_SECONDS,
                    len(self._items),
                )
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        # 收尾必须**排空**队列：_run 收到停机信号时只写完当前批，而 flush()
        # 单批最多 audit_batch_size 条。容量 10000 / 批 200 时只 flush 一次
        # 会静默丢掉后面 9800 条。同时给整段排空加时限和单批超时——DB 卡住
        # 不能让 lifespan 的 finally 永远不返回。
        deadline = time.monotonic() + _STOP_DRAIN_SECONDS
        while self._items:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "审计队列关闭 %.0fs 内未排空，残余 %d 条丢失",
                    _STOP_DRAIN_SECONDS,
                    len(self._items),
                )
                break
            try:
                await asyncio.wait_for(self.flush(), timeout=remaining)
            except asyncio.CancelledError:
                # flush() 已把该批回队；停机取消不外吞，交给调用方传播。
                # 但排空被打断时要把丢失量显式记下来，否则只看到上层的
                # 泛化 warning，排查时不知道丢了多少审计记录。
                logger.error(
                    "审计队列关闭时被取消，残余 %d 条未落库",
                    len(self._items),
                )
                raise
            except Exception:
                logger.exception("审计队列关闭时刷盘失败，残余 %d 条丢失", len(self._items))
                break

    # ---- 后台刷盘 ------------------------------------------------------

    async def _run(self) -> None:
        interval = max(settings.audit_flush_interval_seconds, 0.05)
        max_retries = max(1, settings.audit_max_retries)
        failures = 0
        while not self._stop.is_set():
            # 失败后退避，避免 DB 故障时每 200ms 打一次无效事务；上限 5s
            delay = interval if failures == 0 else min(interval * (2 ** min(failures, 5)), _MAX_BACKOFF_SECONDS)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                break  # 收到停机信号，剩余批次交给 stop() 收尾
            except TimeoutError:
                pass
            try:
                await self.flush()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                if failures >= max_retries:
                    dropped = 0
                    while self._items and dropped < self._last_batch_size:
                        self._items.popleft()
                        dropped += 1
                    logger.error(
                        "审计批量写入连续 %d 轮失败，丢弃 %d 条无法落库的记录以避免无限重试；"
                        "剩余积压 %d 条",
                        failures,
                        dropped,
                        len(self._items),
                    )
                    failures = 0
                else:
                    logger.exception(
                        "审计批量写入失败（第 %d/%d 轮），积压 %d 条等待重试",
                        failures,
                        max_retries,
                        len(self._items),
                    )

    async def flush(self) -> int:
        """把最多 ``audit_batch_size`` 条记录一次性写入，返回成功条数。"""
        if not self._items:
            self._last_batch_size = 0
            return 0
        batch: list[dict[str, Any]] = []
        # 下限钳到 1：误配 AUDIT_BATCH_SIZE<=0 会让 while 永不 pop，队列只增不减
        limit = max(1, settings.audit_batch_size)
        while self._items and len(batch) < limit:
            batch.append(self._items.popleft())
        self._last_batch_size = len(batch)

        try:
            from app.db.session import async_session_factory
            from app.kernel.models import OperationLog

            async with async_session_factory() as session:
                for row in batch:
                    session.add(OperationLog(**row))
                await session.commit()
            self._last_batch_size = 0
            return len(batch)
        except (Exception, asyncio.CancelledError):
            # CancelledError 在 3.12 属于 BaseException：停机取消若命中 commit，
            # 已出队的整批记录必须回到队头，否则这批审计永久消失。
            self._items.extendleft(reversed(batch))
            raise


audit_writer = AuditWriteQueue()

# 审计积压 gauge（Phase 11-4）：积压持续增长说明落库速度跟不上了，
# 是「审计悄悄堆积」的直接观测点。
from app.core import metrics as _metrics  # noqa: E402

_metrics.register_gauge(
    "fastcms_audit_queue_depth",
    "Number of audit log records pending batch write.",
    audit_writer.depth,
)
