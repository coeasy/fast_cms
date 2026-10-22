from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, apply_user_scope, get_data_scope
from app.core.export import ExportColumn, build_rows, csv_response
from app.core.tz import local_text
from app.modules.config.models import DictType
from app.modules.content.models import Notice
from app.modules.schedule.models import JobLog
from app.modules.system.models import Role, User

# 单次导出的行数上限：再大的量应该让调用方改用筛选条件分批导，
# 而不是把整个表读进内存拼一个 CSV
EXPORT_LIMIT = 10000


@dataclass(frozen=True)
class ExportSpec:
    key: str
    title: str
    permission: str
    header: tuple[str, ...]
    columns: tuple[ExportColumn, ...]


def _time(value: Any) -> str:
    return "" if value is None else local_text(value, "%Y-%m-%d %H:%M:%S%z")


def _local(key: str) -> Callable[[Any], str]:
    """时间列的取值器：``_local("created_at")(user) == _time(user.created_at)``。

    ``ExportColumn.value`` 收的是整行 ORM 对象，datetime 字段不能把 ``_time``
    直接当 value 传——那会把整个对象交给时区转换然后炸在 ``tzinfo`` 上。
    """

    def get(item: Any) -> str:
        return _time(getattr(item, key, None))

    return get


def _dept_name(item: User) -> str:
    return item.dept.name if item.dept else ""


def _roles(item: User) -> str:
    return "、".join(role.name for role in item.roles)


def _permissions(item: Role) -> str:
    return "、".join(code.code for code in item.permissions)


def _dict_items(item: DictType) -> str:
    return "、".join(f"{entry.label}={entry.value}" for entry in item.items)


USER_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("username", "用户名"),
    ExportColumn("nickname", "昵称"),
    ExportColumn("dept_name", "部门", _dept_name),
    ExportColumn("roles", "角色", _roles),
    ExportColumn("email", "邮箱"),
    ExportColumn("phone", "手机号"),
    ExportColumn("status", "状态"),
    ExportColumn("is_banned", "是否封禁", lambda item: item.is_banned),
    ExportColumn("ban_reason", "封禁原因"),
    ExportColumn("last_login_ip", "最近登录 IP"),
    ExportColumn("last_login_at", "最近登录时间", _local("last_login_at")),
    ExportColumn("created_at", "创建时间", _local("created_at")),
)

ROLE_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("name", "角色名称"),
    ExportColumn("code", "角色编码"),
    ExportColumn("data_scope", "数据范围"),
    ExportColumn("status", "状态"),
    ExportColumn("sort", "排序"),
    ExportColumn("permissions", "权限", _permissions),
    ExportColumn("created_at", "创建时间", _local("created_at")),
)

DICT_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("name", "字典名称"),
    ExportColumn("code", "字典编码"),
    ExportColumn("status", "状态"),
    ExportColumn("items", "字典项", _dict_items),
    ExportColumn("remark", "备注"),
    ExportColumn("created_at", "创建时间", _local("created_at")),
)

NOTICE_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("title", "标题"),
    ExportColumn("is_top", "置顶", lambda item: item.is_top),
    ExportColumn("status", "状态"),
    ExportColumn("published_at", "发布时间", _local("published_at")),
    ExportColumn("created_at", "创建时间", _local("created_at")),
)

JOB_LOG_COLUMNS = (
    ExportColumn("id", "ID"),
    ExportColumn("job_name", "任务名称"),
    ExportColumn("job_code", "任务编码"),
    ExportColumn("trigger_type", "触发方式"),
    ExportColumn("status", "结果"),
    ExportColumn("duration_ms", "耗时(ms)"),
    ExportColumn("error", "错误信息"),
    ExportColumn("created_at", "执行时间", _local("created_at")),
)

SPECS: dict[str, ExportSpec] = {
    "users": ExportSpec(
        key="users",
        title="用户",
        permission="system:user:export",
        header=("ID", "用户名", "昵称", "部门", "角色", "邮箱", "手机号", "状态", "是否封禁", "封禁原因", "最近登录IP", "最近登录时间", "创建时间"),
        columns=USER_COLUMNS,
    ),
    "roles": ExportSpec(
        key="roles",
        title="角色",
        permission="system:role:export",
        header=("ID", "角色名称", "角色编码", "数据范围", "状态", "排序", "权限", "创建时间"),
        columns=ROLE_COLUMNS,
    ),
    "dicts": ExportSpec(
        key="dicts",
        title="字典",
        permission="system:dict:export",
        header=("ID", "字典名称", "字典编码", "状态", "字典项", "备注", "创建时间"),
        columns=DICT_COLUMNS,
    ),
    "notices": ExportSpec(
        key="notices",
        title="公告",
        permission="system:notice:export",
        header=("ID", "标题", "置顶", "状态", "发布时间", "创建时间"),
        columns=NOTICE_COLUMNS,
    ),
    "job-logs": ExportSpec(
        key="job-logs",
        title="任务日志",
        permission="schedule:log:export",
        header=("ID", "任务名称", "任务编码", "触发方式", "结果", "耗时(ms)", "错误信息", "执行时间"),
        columns=JOB_LOG_COLUMNS,
    ),
}


async def _user_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    from sqlalchemy.orm import selectinload

    scope = await get_data_scope(db, ctx)
    statement = select(User).options(selectinload(User.dept), selectinload(User.roles))
    statement = apply_user_scope(statement, ctx, scope)
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(
            or_(User.username_normalized.like(like), func.lower(User.nickname).like(like))
        )
    if status:
        statement = statement.where(User.status == status)
    return statement.order_by(User.id.desc())


async def _role_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    from sqlalchemy.orm import selectinload

    del ctx
    statement = select(Role).options(selectinload(Role.permissions), selectinload(Role.departments))
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(or_(func.lower(Role.name).like(like), Role.code.like(like)))
    if status:
        statement = statement.where(Role.status == status)
    return statement.order_by(Role.sort, Role.id)


async def _dict_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    from sqlalchemy.orm import selectinload

    del ctx
    statement = select(DictType).options(selectinload(DictType.items))
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(or_(func.lower(DictType.name).like(like), DictType.code.like(like)))
    if status:
        statement = statement.where(DictType.status == status)
    return statement.order_by(DictType.id.desc())


async def _notice_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    del db, ctx
    statement = select(Notice)
    if keyword:
        statement = statement.where(Notice.title.like(f"%{keyword.strip()}%"))
    if status:
        statement = statement.where(Notice.status == status)
    return statement.order_by(Notice.id.desc())


async def _job_log_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    del ctx
    statement = select(JobLog)
    if keyword:
        statement = statement.where(JobLog.job_name.like(f"%{keyword.strip()}%"))
    if status:
        statement = statement.where(JobLog.status == status)
    return statement.order_by(JobLog.id.desc())


_QUERIES = {
    "users": _user_statement,
    "roles": _role_statement,
    "dicts": _dict_statement,
    "notices": _notice_statement,
    "job-logs": _job_log_statement,
}


async def export(
    db: AsyncSession,
    ctx: AuthContext,
    spec_key: str,
    *,
    keyword: str | None = None,
    status: str | None = None,
    limit: int = EXPORT_LIMIT,
) -> tuple[Any, int, bool]:
    spec = SPECS.get(spec_key)
    if spec is None:
        from app.core.errors import AppError

        raise AppError("EXPORT_SPEC_UNKNOWN", f"不支持的导出对象：{spec_key}", 404)
    statement = await _QUERIES[spec_key](db, ctx, keyword, status)
    # 多取一行即可判断是否被截断，不需要额外 count 全表
    statement = statement.limit(limit + 1)
    rows: list[Any] = list((await db.execute(statement)).scalars().all())
    truncated = len(rows) > limit
    response, count, _ = csv_response(
        spec.header,
        build_rows(rows[:limit], spec.columns),
        f"{spec.title}.csv",
        limit=limit,
    )
    # 导出敏感数据必须留痕：谁在什么时候导出了哪一类、多少行
    await _audit_export(db, ctx, spec, count, truncated)
    return response, count, truncated


async def _audit_export(
    db: AsyncSession, ctx: AuthContext, spec: ExportSpec, count: int, truncated: bool
) -> None:
    from app.modules.audit.service import write_export_audit

    await write_export_audit(
        db,
        user_id=ctx.user.id,
        username=ctx.user.username,
        module=spec.key.split("-")[0],
        action=f"{spec.key}.export",
        path=f"/export/{spec.key}",
        detail=f"rows={count} truncated={1 if truncated else 0}",
    )
