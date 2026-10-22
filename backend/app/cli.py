from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import shutil
import string
import sys
from pathlib import Path

from sqlalchemy import select, text, update

from app.core.config import DEFAULT_ADMIN_USERNAME, settings
from app.db.base import utcnow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fastcms", description="Fast CMS 管理后台命令行")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化数据库结构并写入基线数据")
    init.add_argument("--skip-seed", action="store_true", help="只执行迁移，不写入基线数据")

    db = sub.add_parser("db", help="数据库迁移管理")
    db.add_argument("action", choices=["upgrade", "current", "check"])

    sub.add_parser("seed", help="写入或同步基线数据")
    sub.add_parser("sync-rbac", help="同步权限与内置角色（不写入业务数据）")

    reset = sub.add_parser("reset-password", help="重置指定用户密码")
    reset.add_argument("username")
    reset.add_argument("--password", default="")
    reset.add_argument("--random", action="store_true", help="生成随机密码")

    backup = sub.add_parser("backup", help="备份 SQLite 数据库")
    backup.add_argument("--output", default="")

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


async def _db(action: str) -> None:
    from app.db.migrate import backup_sqlite, ensure_database, head_revision
    from app.db.session import engine

    if action == "upgrade":
        from alembic import command

        from app.db.migrate import alembic_config

        if settings.is_sqlite:
            backup = backup_sqlite()
            if backup:
                print(f"[db] 已备份到 {backup}")
        await asyncio.to_thread(command.upgrade, alembic_config(), "head")
        print("[db] 迁移完成")
        return
    if action == "current":
        async with engine.connect() as connection:
            has_table = await connection.run_sync(lambda sync: sync.dialect.has_table(sync, "alembic_version"))
            current = None
            if has_table:
                current = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print(f"当前迁移版本：{current or '空'}")
        return
    await ensure_database()
    print(f"[db] 数据库迁移版本正常，目标版本：{head_revision()}")


def _require_admin_password() -> None:
    """生产环境禁止使用内置默认口令，提前给出可操作的提示。"""
    try:
        settings.admin_password
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
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in candidate) and any(c.isdigit() for c in candidate):
            return candidate


async def _reset_password(username: str, password: str, random_password: bool) -> None:
    from app.core.security import hash_password, validate_password_strength
    from app.db.migrate import ensure_database
    from app.db.session import session_scope
    from app.modules.config.service import password_min_length
    from app.modules.system.models import User, UserSession

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
        user.password_hash = hash_password(new_password)
        user.must_change_password = True
        user.token_version += 1
        user.failed_login_count = 0
        user.locked_until = None
        await db.execute(
            update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=utcnow())
        )
    print(f"用户 {username} 的密码已重置：{new_password}")
    print("该用户下次登录时必须修改密码。")


def _backup(output: str) -> None:
    from app.db.migrate import backup_sqlite

    if output:
        source = Path(settings.db_url.split("///", 1)[-1])
        target = Path(output)
        if not source.exists():
            raise SystemExit("数据库文件不存在")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print(f"备份已写入：{target}")
        return
    safety = backup_sqlite()
    if safety is None:
        raise SystemExit("仅 SQLite 支持此备份命令；MySQL 请使用 mysqldump")
    print(f"备份已写入：{safety}")


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
    from app.modules.security.models import IpRule

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
    from app.db.migrate import ensure_database
    from app.db.session import engine, session_scope
    from app.modules.config.service import load_timezone_from_db
    from app.modules.schedule.scheduler import scheduler_service

    configure_logging()
    await init_cache()
    try:
        await ensure_database()
        # 独立进程也要用数据库里的系统时区，否则任务时区会和 Web 进程不一致
        async with session_scope() as db:
            zone_name = await load_timezone_from_db(db)
        await scheduler_service.start()
        print(f"[scheduler] 独立调度器已启动（时区 {zone_name}），按 Ctrl+C 停止")
        while True:
            await asyncio.sleep(max(10, settings.scheduler_sync_seconds))
            await scheduler_service.sync_all()
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


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    args = _parser().parse_args(argv)

    if args.command == "init":
        asyncio.run(_init(args.skip_seed))
    elif args.command == "db":
        asyncio.run(_db(args.action))
    elif args.command == "seed":
        asyncio.run(_seed())
    elif args.command == "sync-rbac":
        asyncio.run(_sync_rbac())
    elif args.command == "reset-password":
        asyncio.run(_reset_password(args.username, args.password, args.random))
    elif args.command == "backup":
        _backup(args.output)
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


if __name__ == "__main__":
    main()
