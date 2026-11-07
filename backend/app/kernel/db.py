"""内核级数据库工具：统一提交语义、事务边界与操作日志写入。

三件事，都只在这里做一次：

1. ``commit`` —— 把 ``IntegrityError`` 统一翻译成语义化 ``AppError``，避免每个模块
   各写一份 try/except，也避免「裸 ``db.commit()`` 抛原始异常 → 500」这类退化。
2. ``transactional`` —— 显式工作单元。进入即开启事务，正常返回提交，抛异常回滚，
   让「全成功或全失败」的场景不再靠散落的 ``commit()`` 拼出来。
3. ``after_commit`` —— 把「事务提交之后才能做的副作用」（发事件、推送通知）
   从「靠注释提醒顺序」变成「靠机制保证顺序」。

``log_operation`` 供业务模块在同一事务里补写审计记录。
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import api_path
from app.core.errors import ERROR_CODE_STATUS, AppError, get_request_id
from app.core.request_context import current_context, request_duration_ms
from app.kernel.authz import AuthContext
from app.kernel.models import OperationLog

logger = logging.getLogger("fastcms.db")

AfterCommit = Callable[[], Awaitable[None]]

# 当前请求内「已登记、等提交后执行」的副作用回调。
# 用 ContextVar 而不是模块级列表：并发请求各持一份，互不串扰。
_after_commit: ContextVar[list[AfterCommit] | None] = ContextVar("fastcms_after_commit", default=None)


def after_commit(callback: AfterCommit) -> None:
    """登记一个「事务提交成功后再执行」的回调。

    典型用法是把事件发布挂上去：

        after_commit(lambda: events.publish(
            "notice.published",
            notice_id=item.id,
            title=item.title,
            content=item.content,
            published_at=item.published_at,
            publisher=ctx.user.username,
        ))
        await commit(db)

    这样即使中间任何一步失败、事务被回滚，事件也不会发出去——「发出去了但库里没有」
    是排查成本最高的一类不一致。回调在 ``commit()`` 里按登记顺序串行执行，
    单个回调抛异常只记日志，不影响已提交的业务结果，也不影响后续回调。
    """
    pending = _after_commit.get()
    if pending is None:
        pending = []
        _after_commit.set(pending)
    pending.append(callback)


def discard_after_commit() -> None:
    """丢弃已登记的回调（事务回滚时调用）。"""
    _after_commit.set([])


async def _drain_after_commit() -> None:
    pending = _after_commit.get()
    if not pending:
        return
    # 先清空再执行：回调里如果又写库并 commit，不会重入本函数重复派发
    _after_commit.set([])
    for callback in pending:
        try:
            await callback()
        except Exception:
            logger.exception("after_commit 回调执行失败")


async def commit(db: AsyncSession, *, conflict_code: str = "DATA_CONFLICT") -> None:
    """提交事务；唯一约束冲突转成语义化 ``AppError``。

    ``conflict_code`` 让调用方给出更精确的语义：唯一约束冲突的真实原因只有调用方
    知道（主题编码重复、用户名重复、角色编码重复），让它留在 kernel 里猜，只能得到
    一个含糊的「数据冲突」，前端也没法给出针对性提示。状态码不在这里写死——
    ``ERROR_CODE_STATUS`` 是单一事实源，HTTP 翻译统一收敛到全局异常处理器。

    因为错误码是参数，``scripts/check_contract.py`` 的 AST 校验看不到它（该文件登记在
    ``DYNAMIC_ERROR_CODE_CALLERS``），所以这里**在运行时补一道校验**：码没登记就直接
    抛 ``ValueError``，避免把拼错的码带到线上变成一个没有文案的 500。
    """
    if conflict_code not in ERROR_CODE_STATUS:
        raise ValueError(f"未登记的错误码 {conflict_code!r}，请先补进 core/errors.py 的 ERROR_CODE_STATUS")
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        discard_after_commit()
        raise AppError(conflict_code) from exc
    await _drain_after_commit()


def transactional(
    func: Callable[..., Awaitable[Any]] | None = None, *, conflict_code: str = "DATA_CONFLICT"
) -> Any:
    """把 service 函数包成一个显式工作单元：正常返回提交，抛异常整事务回滚。

    进入时不需要显式 ``begin``——session 在第一次执行时自动开启事务；这里只负责收尾。

    **两条使用边界，都是踩过的坑**：

    1. 只用于「全成功或全失败」的场景（角色授权、权限授权、重置密码这类单次多写动作）。
       批量操作要的是「逐条提交 + 返回失败明细」，套上原子事务会让第 37 条失败把前
       36 条一起回滚，与产品预期相反。
    2. **不要在「调用方会 catch 后继续用同一个 session」的地方用**。整事务
       ``rollback()`` 会让 session 里所有对象过期（不只是本函数写过的），调用方
       再读 ``ctx.user.id`` 就会触发一次同步刷新，在 async 环境里直接
       ``MissingGreenlet``。批量循环正是这种形态，所以它用显式 commit 而不是本装饰器。

    这里**没有**用 SAVEPOINT 做「局部回滚」：pysqlite 在「SAVEPOINT 是事务里的第一条
    语句」时会把 ``RELEASE SAVEPOINT`` 退化成提交，回滚不再生效（见
    ``docs/模块与分层规范.md`` 的「环境陷阱」）。用 SAVEPOINT 换来的是「看起来更精细
    但只在特定路径下成立」的语义，不如不用。

    约定：被装饰函数的第一个位置参数（或关键字参数 ``db``）必须是 ``AsyncSession``。
    """

    def decorate(target: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(target)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            db = kwargs.get("db")
            if db is None:
                db = args[0] if args else None
            if not isinstance(db, AsyncSession):
                raise TypeError(
                    f"@transactional 要求首个位置参数或 db 关键字是 AsyncSession，"
                    f"{target.__qualname__} 收到的是 {type(db).__name__}"
                )
            try:
                result = await target(*args, **kwargs)
            except BaseException:
                await db.rollback()
                discard_after_commit()
                raise
            await commit(db, conflict_code=conflict_code)
            return result

        return wrapper

    if func is not None:
        return decorate(func)
    return decorate


async def log_operation(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    module: str,
    action: str,
    detail: str | None = None,
    method: str = "POST",
    path: str = "",
) -> None:
    """写一条操作日志；调用方负责在同一事务里提交。

    ``duration_ms`` 从请求上下文推导真实耗时（B-1 ②）：service 层写审计时
    不应再硬编码 0，否则「操作记录」页的耗时统计失真。
    """
    http = current_context()
    db.add(
        OperationLog(
            user_id=ctx.user.id,
            username=ctx.user.username,
            module=module,
            action=action,
            method=method,
            path=api_path(path)[:255] if path else "",
            status_code=200,
            success=True,
            duration_ms=request_duration_ms(),
            ip=http.ip,
            user_agent=http.user_agent,
            detail=detail,
            request_id=get_request_id() or None,
        )
    )
