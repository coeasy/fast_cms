"""启动引导与生命周期（A-6：从 ``main.py`` 拆出）。

原来启动引导、种子、初始管理员提示与 ``create_app`` 挤在 ``main.py``（271 行）。
拆出本模块让 ``main.py`` 保持短小（≤80 行），只保留装配根与薄薄的 ``lifespan`` 包装；
真正的启动/关闭逻辑（迁移、种子、时区、调度器、缓存）都在这里。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap.models import BootstrapVersion
from app.bootstrap.seed import SEED_VERSION, seed_database, sync_permission_codes
from app.core import ipgeo
from app.core.boot_lock import BootLock
from app.core.cache import close_cache, init_cache
from app.core.config import DEFAULT_ADMIN_USERNAME, settings
from app.core.logging import configure_logging
from app.db.migrate import ensure_database
from app.db.session import engine, session_scope
from app.modules.ops.schedule.scheduler import scheduler_service
from app.modules.sys.config.service import load_timezone_from_db

logger = logging.getLogger("fastcms")


def print_initial_admin_notice() -> None:
    banner = "=" * 68
    print(banner)
    print("  已创建初始管理员账号")
    print(f"  用户名：{DEFAULT_ADMIN_USERNAME}")
    if settings.is_production:
        print("  初始密码：取自 INITIAL_ADMIN_PASSWORD，首次登录后必须修改")
    else:
        print(f"  初始密码：{settings.admin_password}（可用 INITIAL_ADMIN_PASSWORD 覆盖）")
    print(banner)


async def ensure_seed() -> None:
    async with session_scope() as db:
        version = await db.get(BootstrapVersion, "seed")
        if version is not None and version.version == SEED_VERSION:
            # 权限码是代码里的真相，旧库必须补齐新增项，否则升级后接口直接 403
            await sync_permission_codes(db)
            return
        created = await seed_database(db)
    if created:
        print_initial_admin_notice()


def embedded_multiworker_policy() -> str:
    """嵌入式调度器 + 多 worker 的启动策略（A-9 / 3-6）。

    返回：
    - ``"ok"``：非 embedded 或单 worker，正常启动；
    - ``"allow"``：embedded + 多 worker 且显式开启 ``scheduler_embedded_multiworker``，
      放行但调用方必须打启动警告（租约与 max_instances=1 兜底）；
    - ``"raise"``：embedded + 多 worker 且未显式开启，拒绝启动（默认安全）。
    """
    if settings.scheduler_mode != "embedded" or settings.app_workers <= 1:
        return "ok"
    return "allow" if settings.scheduler_embedded_multiworker else "raise"


async def _shutdown_resources() -> None:
    """按启动的逆序释放资源；**启动中途失败也会走到这里**。

    ``try/finally`` 只包住 ``yield`` 是不够的：``init_cache()``、``ensure_database()``、
    ``scheduler_service.start()`` 等任一步抛异常，原来留下的 Redis 连接池、已
    firing 的 APScheduler、审计后台任务和 engine 都不会被关闭。

    每一步独立兜底：任一清理抛异常或被取消，也要把其余资源关干净；取消信号
    收集到最后再抛，避免中途截断清理链。
    """
    from app.kernel.audit import audit_writer

    steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("scheduler", scheduler_service.shutdown),
    ]
    if settings.app_env != "test":
        steps.append(("audit-writer", audit_writer.stop))
    steps.append(("cache", close_cache))
    steps.append(("engine", engine.dispose))

    cancelled: asyncio.CancelledError | None = None
    for name, fn in steps:
        try:
            await fn()
        except asyncio.CancelledError as exc:
            cancelled = exc
            logger.warning("关闭 %s 时收到取消信号，其余资源继续清理", name)
        except Exception:
            logger.exception("关闭 %s 失败（忽略，继续清理其余资源）", name)
    if cancelled is not None:
        raise cancelled


@asynccontextmanager
async def app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """应用启动 / 关闭的完整引导。"""
    policy = embedded_multiworker_policy()
    if policy == "raise":
        raise RuntimeError(
            "APP_WORKERS>1 时禁止嵌入式调度器（多进程各自持有调度器会让任务重复执行、"
            "next_run_at 抖动）。请设置 SCHEDULER_MODE=standalone 或 disabled；"
            "确需强行开启请设置 SCHEDULER_EMBEDDED_MULTIWORKER=true 并自担风险。"
        )
    if policy == "allow":
        # 显式放开：多 worker 各自持有嵌入式调度器。租约（sys_job_lock）与
        # max_instances=1 仍是最后防线，但 next_run_at 可能抖动、任务可能跨进程
        # 竞争，故必须警告。见 docs/部署形态与一致性矩阵.md（A-9 / 3-6）。
        logger.warning(
            "SCHEDULER_MODE=embedded 且 APP_WORKERS>1 已显式开启："
            "多 worker 各自持有嵌入式调度器，任务可能跨进程竞争、next_run_at 可能抖动，"
            "依赖任务租约与 max_instances=1 兜底。生产建议 SCHEDULER_MODE=standalone。"
        )
    # 整个「启动 + 服务期」都在 try 里：任何一步启动失败都必须走同一套清理，
    # 否则模块级单例上会留下半启动状态（详见 _shutdown_resources 文档）。
    try:
        configure_logging()
        await init_cache()
        # IP 归属地库提前加载并校验：xdb 缺失只告警，不阻断启动
        await ipgeo.startup()
        # 多 worker 首启时并发执行迁移/种子会让 SQLite 直接报 locked，用文件锁串行化引导过程
        boot_lock = BootLock(settings.data_root / "bootstrap.lock")
        if not await asyncio.to_thread(boot_lock.acquire):
            raise RuntimeError(
                f"等待启动锁超时，请确认是否有其他进程占用 {settings.data_root / 'bootstrap.lock'}"
            )
        try:
            await ensure_database()
            await ensure_seed()
            # 系统时区以数据库参数为准（环境变量只是初值），必须在调度器启动前加载
            async with session_scope() as db:
                system_timezone = await load_timezone_from_db(db)
        finally:
            boot_lock.release()
        if settings.scheduler_python_enabled and settings.is_production:
            # P1-12：Python 任务是同权限子进程，生产开启必须显式告警，防止误开
            logger.warning(
                "SCHEDULER_PYTHON_ENABLED=true：Python 脚本任务以与应用进程相同权限的子进程执行，"
                "开启即扩大代码执行面，仅允许受信管理员使用（等价进程权限，见 docs/定时任务.md）"
            )
        if settings.scheduler_mode == "embedded":
            await scheduler_service.start()
        # B-7 / Phase 11-3：中间件兜底审计改走批量队列。测试环境保持同步直写——
        # 「请求返回后立刻断言操作日志」的用例会被 200ms 刷盘间隔弄成偶发失败；
        # 生产（非 test env）才启用攒批。
        if settings.app_env != "test":
            from app.kernel.audit import audit_writer

            await audit_writer.start()
        logger.info(
            "application started env=%s version=%s timezone=%s",
            settings.app_env,
            settings.app_version,
            system_timezone,
        )
        yield
    finally:
        await _shutdown_resources()
