from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import secrets
import shutil
import string
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text, update

from app.core.config import DEFAULT_ADMIN_USERNAME, settings
from app.db.base import utcnow

logger = logging.getLogger("fastcms.scheduler")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fastcms", description="Fast CMS 管理后台命令行")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化数据库结构并写入基线数据")
    init.add_argument("--skip-seed", action="store_true", help="只执行迁移，不写入基线数据")

    db = sub.add_parser("db", help="数据库迁移管理")
    db.add_argument("action", choices=["upgrade", "downgrade", "current", "check"])
    # P1-11：降级演练需要显式指定目标版本；默认 -1（上一版），base 为全量回滚
    db.add_argument("--revision", default="-1", help="downgrade 目标版本，默认 -1（上一版）")

    sub.add_parser("seed", help="写入或同步基线数据")
    sub.add_parser("sync-rbac", help="同步权限与内置角色（不写入业务数据）")

    reset = sub.add_parser("reset-password", help="重置指定用户密码")
    reset.add_argument("username")
    reset.add_argument("--password", default="")
    reset.add_argument("--random", action="store_true", help="生成随机密码")

    backup = sub.add_parser("backup", help="备份 SQLite 数据库")
    backup.add_argument("--output", default="")
    backup.add_argument(
        "--with-uploads",
        action="store_true",
        help="同时归档上传文件目录，产出一个可直接搬移的 zip（库 + uploads）",
    )

    restore = sub.add_parser("restore", help="恢复 SQLite 数据库备份")
    restore.add_argument("path")
    restore.add_argument("--yes", action="store_true")

    unban = sub.add_parser("unban-ip", help="停用指定 IP/CIDR 的封禁规则（IP 守卫自救通道）")
    unban.add_argument("target", help="IP 或 CIDR，例如 203.0.113.7 或 203.0.113.0/24")
    unban.add_argument("--all", action="store_true", help="连 allow 规则一起停用（默认只停 block）")

    sub.add_parser("scheduler", help="以独立进程运行调度器")

    gen = sub.add_parser("gen", help="根据现有数据表生成 CRUD 代码")
    gen.add_argument("--table", required=True)
    gen.add_argument("--module", required=True)
    gen.add_argument("--resource", default=None)
    gen.add_argument("--title", default=None)
    gen.add_argument("--write", action="store_true", help="写入 generated/ 目录")

    openapi = sub.add_parser("export-openapi", help="导出 OpenAPI 文档")
    openapi.add_argument("--output", default="openapi.json")

    sub.add_parser("check-permissions", help="校验路由权限码与基线声明是否一致")

    sub.add_parser(
        "migrate-registry",
        help="干跑导出三张注册表（权限/页面/菜单）存量条目为各模块的候选清单片段（不落盘）",
    )
    return parser


async def _init(skip_seed: bool) -> None:
    from app.bootstrap.models import BootstrapVersion
    from app.bootstrap.seed import SEED_VERSION, seed_database
    from app.db.migrate import ensure_database
    from app.db.session import session_scope

    if not skip_seed:
        _require_admin_password()
    await ensure_database()
    if skip_seed:
        print("[init] 已跳过基线数据写入")
        return
    created_admin = False
    async with session_scope() as db:
        version = await db.get(BootstrapVersion, "seed")
        if version is not None and version.version == SEED_VERSION:
            print("[init] 基线数据已是最新版本")
        else:
            created_admin = await seed_database(db)
    if created_admin:
        print("=" * 68)
        print("  初始管理员已创建")
        print(f"  用户名：{DEFAULT_ADMIN_USERNAME}")
        if settings.is_production:
            print("  初始密码：取自 INITIAL_ADMIN_PASSWORD，首次登录后必须修改")
        else:
            print(f"  初始密码：{settings.admin_password}（可用 INITIAL_ADMIN_PASSWORD 覆盖）")
        print("=" * 68)
    print("[init] 初始化完成")


async def _db(action: str, revision: str = "-1") -> None:
    from app.db.migrate import backup_sqlite
    from app.db.session import engine

    if action in ("upgrade", "downgrade"):
        from alembic import command

        from app.db.migrate import alembic_config, migration_lock

        # 数据库级迁移锁：多副本并发入口只允许一个真正迁移，其余等待后空操作
        async with migration_lock():
            if action == "upgrade" and settings.is_sqlite:
                backup = backup_sqlite()
                if backup:
                    print(f"[db] 已备份到 {backup}")
            target = "head" if action == "upgrade" else revision
            if action == "upgrade":
                await asyncio.to_thread(command.upgrade, alembic_config(), target)
            else:
                await asyncio.to_thread(command.downgrade, alembic_config(), target)
        print(f"[db] {'迁移' if action == 'upgrade' else '回滚'}完成（目标 {target}）")
        return
    if action == "current":
        async with engine.connect() as connection:
            has_table = await connection.run_sync(lambda sync: sync.dialect.has_table(sync, "alembic_version"))
            current = None
            if has_table:
                current = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print(f"当前迁移版本：{current or '空'}")
        return
    from app.db.migrate import check_database

    current, head = await check_database()
    if current == head:
        print(f"[db] 数据库迁移版本正常，目标版本：{head}")
    else:
        print(
            f"[db] 数据库迁移版本落后（当前 {current or '空'}，目标 {head}）。"
            "请执行 `python -m app.cli db upgrade`。"
        )
        raise SystemExit(1)


def _require_admin_password() -> None:
    """生产环境禁止使用内置默认口令，提前给出可操作的提示。"""
    try:
        # 访问属性触发校验：production + 默认口令会抛 ValueError
        _ = settings.admin_password
    except ValueError as exc:
        print(f"[init] {exc}")
        raise SystemExit(2) from exc


async def _seed() -> None:
    from app.bootstrap.seed import seed_database
    from app.db.migrate import ensure_database
    from app.db.session import session_scope

    _require_admin_password()
    await ensure_database()
    async with session_scope() as db:
        created_admin = await seed_database(db)
    print("[seed] 基线数据写入完成" if not created_admin else f"[seed] 初始管理员已创建：{DEFAULT_ADMIN_USERNAME}")


async def _sync_rbac() -> None:
    from app.bootstrap.seed import sync_rbac
    from app.db.migrate import ensure_database
    from app.db.session import session_scope

    await ensure_database()
    async with session_scope() as db:
        await sync_rbac(db)
    print("[seed] 权限与内置角色同步完成")


def _generated_password(min_length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    length = max(16, min_length)
    # 拒绝采样有上限：alphabet 或 secrets.choice 一旦被改动/打桩，无界 while
    # 会让 reset-password 永久卡住且没有任何可观测信息。
    for _ in range(64):
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in candidate) and any(c.isdigit() for c in candidate):
            return candidate
    return "Aa1" + "x" * (length - 3)


async def _reset_password(username: str, password: str, random_password: bool) -> None:
    from app.core.security import hash_password_async, validate_password_strength
    from app.db.migrate import ensure_database
    from app.db.session import session_scope
    from app.kernel.models import User, UserSession
    from app.modules.sys.config.service import password_min_length

    await ensure_database()
    async with session_scope() as db:
        user = (
            await db.execute(select(User).where(User.username_normalized == username.strip().lower()))
        ).scalars().first()
        if user is None:
            raise SystemExit(f"用户不存在：{username}")
        min_length = await password_min_length(db)
        new_password = _generated_password(min_length) if random_password or not password else password
        valid, message = validate_password_strength(new_password, user.username, min_length=min_length)
        if not valid:
            raise SystemExit(f"密码不符合要求：{message}")
        user.password_hash = await hash_password_async(new_password)
        user.must_change_password = True
        user.token_version += 1
        user.failed_login_count = 0
        user.locked_until = None
        await db.execute(
            update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=utcnow())
        )
    print(f"用户 {username} 的密码已重置：{new_password}")
    print("该用户下次登录时必须修改密码。")


def _archive_with_uploads(db_snapshot: Path, target: Path | None) -> None:
    """把库快照与上传目录打成一个 zip，避免「恢复库但丢文件」的半套备份。"""
    import zipfile

    uploads = Path(settings.storage_local_dir).resolve()
    if target is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = db_snapshot.with_name(f"{db_snapshot.stem}-with-uploads-{stamp}.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(db_snapshot, arcname=f"db/{db_snapshot.name}")
        if uploads.is_dir():
            for item in sorted(uploads.rglob("*")):
                if item.is_file():
                    archive.write(item, arcname=f"uploads/{item.relative_to(uploads).as_posix()}")
        else:
            print(f"[backup] 上传目录不存在，仅归档数据库：{uploads}")
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"归档已写入：{target}（{size_mb:.1f} MB）")
    print("[backup] 恢复方式：解包后把 db/ 下的 .db 放回数据库路径，uploads/ 覆盖上传目录")


def _backup(output: str, with_uploads: bool = False) -> None:
    from app.db.migrate import backup_sqlite

    if output:
        source = Path(settings.db_url.split("///", 1)[-1])
        if not source.exists():
            raise SystemExit("数据库文件不存在")
        target = Path(output)
        if with_uploads:
            # 归档模式下 --output 指 zip 包本身；库快照先落到同目录的 .db，
            # 否则会把 zip 自己当成快照打进去（自引用、0 字节）
            snapshot = target.with_suffix(".db") if target.suffix.lower() == ".zip" else target
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, snapshot)
            print(f"库快照已写入：{snapshot}")
            _archive_with_uploads(snapshot, target if target.suffix.lower() == ".zip" else None)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print(f"备份已写入：{target}")
        return
    safety = backup_sqlite()
    if safety is None:
        raise SystemExit("仅 SQLite 支持此备份命令；MySQL 请使用 mysqldump")
    print(f"备份已写入：{safety}")
    if with_uploads:
        _archive_with_uploads(safety, None)


def _restore(path: str, confirmed: bool) -> None:
    if not settings.is_sqlite:
        raise SystemExit("仅 SQLite 支持此恢复命令；MySQL 请使用 mysql 客户端恢复")
    source = Path(path)
    if not source.exists():
        raise SystemExit(f"备份文件不存在：{source}")
    if not confirmed:
        raise SystemExit("恢复操作会覆盖当前数据库，请确认后加 --yes 重新执行")
    from app.db.migrate import backup_sqlite

    safety = backup_sqlite()
    target = Path(settings.db_url.split("///", 1)[-1])
    for suffix in ("-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    shutil.copy2(source, target)
    print(f"数据库已从 {source} 恢复；恢复前备份：{safety or '无'}")
    print("请确认应用进程已停止后重新启动。")


async def _unban_ip(target: str, include_allow: bool) -> None:
    """IP 守卫的自救通道。

    中间件跑在 ASGI 栈里，被自己写的规则挡住时没有产品内手段可以恢复；
    CLI 走的是独立进程，天然不受守卫影响，所以这里是最后一道保险。
    只停用不删除：规则是审计对象，删了就查不出当时为什么封。
    """
    import ipaddress

    from app.core.ipguard import invalidate_snapshot
    from app.db.migrate import ensure_database
    from app.db.session import session_scope
    from app.kernel.models import IpRule

    raw = target.strip()
    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise SystemExit(f"无法解析的 IP/CIDR：{target}（{exc}）") from exc

    await ensure_database()
    async with session_scope() as db:
        rows = (await db.execute(select(IpRule))).scalars().all()
        hit = [rule for rule in rows if _overlaps(rule.target, str(network))]
        if not include_allow:
            hit = [rule for rule in hit if rule.kind == "block"]
        if not hit:
            print(f"没有匹配 {network} 的规则")
            return
        for rule in hit:
            rule.status = "inactive"
        await db.commit()
        await invalidate_snapshot()
    for rule in hit:
        print(f"已停用规则 #{rule.id}：{rule.kind} {rule.target}（scope={rule.scope}，原因：{rule.reason or '未填写'}）")
    print(f"共停用 {len(hit)} 条规则，新的拦截判定最多再等一个快照 TTL 生效。")


def _overlaps(rule_target: str, wanted: str) -> bool:
    """规则网段与目标网段是否有交集。

    用地址整数比大小而不是 ``supernet_of``：后者按 IP 版本分开重载，
    mypy 在 ``IPv4Network | IPv6Network`` 上报不出干净的类型。
    """
    import ipaddress

    try:
        left = ipaddress.ip_network(rule_target, strict=False)
        right = ipaddress.ip_network(wanted, strict=False)
    except ValueError:
        return False
    if left.version != right.version:
        return False
    return int(left.network_address) <= int(right.broadcast_address) and int(
        right.network_address
    ) <= int(left.broadcast_address)


async def _scheduler() -> None:
    from app.core.cache import close_cache, init_cache
    from app.core.logging import configure_logging
    from app.core.module import discover_modules
    from app.core.tz import current_timezone
    from app.db.migrate import ensure_database
    from app.db.session import engine, session_scope
    from app.modules.ops.schedule.scheduler import scheduler_service
    from app.modules.sys.config.service import load_timezone_from_db

    configure_logging()
    await init_cache()
    try:
        await ensure_database()
        # 独立进程不经过 create_app()，但事件订阅（notify 的 job.finished、
        # schedule 的时区变更）是在各模块 module.py 里注册的。不加载清单就发布
        # 事件，等于把「任务失败通知」静默丢弃。
        discover_modules()
        # 独立进程也要用数据库里的系统时区，否则任务时区会和 Web 进程不一致
        async with session_scope() as db:
            zone_name = await load_timezone_from_db(db)
        await scheduler_service.start()
        print(f"[scheduler] 独立调度器已启动（时区 {zone_name}），按 Ctrl+C 停止")
        while True:
            await asyncio.sleep(max(10, settings.scheduler_sync_seconds))
            try:
                await scheduler_service.sync_all()
                # 事件总线是进程内的：Web 进程改了时区，独立调度器收不到通知。
                # 每轮同步顺手比对一次库里的时区，变了就重排任务，避免「改完时区
                # 必须重启调度器」这种隐性运维要求。
                # 注意 load_timezone_from_db 自身会 set_timezone，所以必须在调用前取值。
                before = current_timezone()
                async with session_scope() as db:
                    latest = await load_timezone_from_db(db)
                if latest != before:
                    await scheduler_service.resync_timezone()
                    print(f"[scheduler] 时区已切换为 {latest}，任务已按新时区重排")
            except asyncio.CancelledError:
                raise
            except Exception:
                # 一次 DB 瞬断不能把整个调度进程带进 finally 永久停机：
                # 记录异常后等下一轮重试（本地 embedded/standalone 都没有
                # compose 的 restart 兜底）。
                logger.exception("调度器同步轮次失败，将在下一轮重试")
    finally:
        await scheduler_service.shutdown()
        await close_cache()
        await engine.dispose()


async def _gen(table: str, module: str, resource: str | None, title: str | None, write: bool) -> None:
    from app.codegen.generator import GENERATED_ROOT, CodeGenerator
    from app.db.session import engine

    generator = CodeGenerator(engine)
    files = await generator.generate(table=table, module=module, resource=resource, title=title)
    if not write:
        for path in files:
            print(path)
        return
    target = GENERATED_ROOT / module / (resource or table)
    written = generator.write_files(files, target)
    for path in written:
        print(path)


def _export_openapi(output: str) -> None:
    from app.main import app

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OpenAPI 已导出：{path}")


def _check_permissions() -> None:
    from app.bootstrap.permissions import PERMISSION_CODES
    from app.main import app

    schema = app.openapi()
    used: set[str] = set()
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            code = operation.get("x-permission")
            if code:
                used.add(code)
    missing = sorted(used - PERMISSION_CODES)
    unused = sorted(PERMISSION_CODES - used)
    if missing:
        print("以下路由权限码未在 bootstrap/permissions.py 中声明：")
        for code in missing:
            print(f"  - {code}")
    if unused:
        print("以下权限码已声明但未被任何路由使用：")
        for code in unused:
            print(f"  - {code}")
    if missing or unused:
        sys.exit(1)
    print(f"权限码校验通过，共 {len(used)} 个。")


def _migrate_registry() -> None:
    """干跑导出三张注册表存量条目为各模块的候选清单片段（A-1）。

    只打印不落盘：权限 / 页面 / 菜单目前是「存量集中 + 增量走清单」双轨制，
    本命令把存量条目按模块分组，输出 ``module.py`` 里 ``permissions=()`` /
    ``pages=()`` / ``menus=()`` 的候选片段，供人工确认后落地。
    菜单是信息架构（一个目录横跨多模块），其分组结果仅供人工参考。
    """
    from pprint import pformat

    from app.bootstrap.registry_migration import build_registry_candidates
    from app.core.module import discover_modules

    existing = {spec.name for spec in discover_modules()}
    candidates = build_registry_candidates()

    total_perms = total_pages = total_menus = 0
    for module in sorted(candidates):
        block = candidates[module]
        total_perms += len(block["permissions"])
        total_pages += len(block["pages"])
        total_menus += len(block["menus"])
        marker = "已有模块清单" if module in existing else "无对应模块清单，需人工归属"
        print(f"\n### 模块 {module}（{marker}）")
        for registry, items in block.items():
            if not items:
                continue
            print(f"{registry}=(")
            for item in items:
                print(f"    {pformat(item, width=96, sort_dicts=False)},")
            print(")")
    print(
        f"\n合计：{len(candidates)} 个分组，权限 {total_perms} 条，"
        f"页面 {total_pages} 条，菜单 {total_menus} 条。"
        "（干跑，未写任何文件）"
    )


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)

    if args.command == "init":
        asyncio.run(_init(args.skip_seed))
    elif args.command == "db":
        asyncio.run(_db(args.action, getattr(args, "revision", "-1")))
    elif args.command == "seed":
        asyncio.run(_seed())
    elif args.command == "sync-rbac":
        asyncio.run(_sync_rbac())
    elif args.command == "reset-password":
        asyncio.run(_reset_password(args.username, args.password, args.random))
    elif args.command == "backup":
        _backup(args.output, with_uploads=args.with_uploads)
    elif args.command == "restore":
        _restore(args.path, args.yes)
    elif args.command == "unban-ip":
        asyncio.run(_unban_ip(args.target, args.all))
    elif args.command == "scheduler":
        try:
            asyncio.run(_scheduler())
        except KeyboardInterrupt:
            print("\n[scheduler] 已停止")
    elif args.command == "gen":
        asyncio.run(_gen(args.table, args.module, args.resource, args.title, args.write))
    elif args.command == "export-openapi":
        _export_openapi(args.output)
    elif args.command == "check-permissions":
        _check_permissions()
    elif args.command == "migrate-registry":
        _migrate_registry()


if __name__ == "__main__":
    main()
