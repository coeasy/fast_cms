from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import AuthContext, apply_user_scope, get_data_scope
from app.core.export import MAX_EXPORT_ROWS, ExportColumn, build_rows, csv_response
from app.core.search import LIKE_ESCAPE, contains
from app.core.tz import local_text
from app.kernel.models import Role, User
from app.modules.content.models import Notice
from app.modules.ops.audit.service import write_export_audit
from app.modules.ops.schedule.models import JobLog
from app.modules.sys.config.models import DictType

# 单次导出的行数上限：唯一事实源在 core/export.py 的 MAX_EXPORT_ROWS。
# 业务导出与审计导出、路由参数、响应头全部引用它，避免多套口径导致截断行为不一致。
EXPORT_LIMIT = MAX_EXPORT_ROWS


@dataclass(frozen=True)
class ExportSpec:
    key: str
    title: str
    permission: str
    header: tuple[str, ...]
    columns: tuple[ExportColumn, ...]
    # 数据范围策略（P0-5）：
    #   - "user"：按当前用户数据范围裁剪（apply_user_scope），仅 user 资源使用；
    #   - "global"：系统全局资源，列表接口与导出接口一致地不裁剪，
    #     属于「显式白名单」——只有权限码本身限制可见性（能导出的人本就可看全量），
    #     必须在 SPECS 里显式声明，禁止静默忽略数据范围。
    scope_policy: str = "global"


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
        # 用户是部门归属资源：导出必须走数据范围，受限管理员不能下载全量用户
        scope_policy="user",
    ),
    "roles": ExportSpec(
        key="roles",
        title="角色",
        permission="system:role:export",
        header=("ID", "角色名称", "角色编码", "数据范围", "状态", "排序", "权限", "创建时间"),
        columns=ROLE_COLUMNS,
        # 系统全局：角色定义不属于某个部门，列表接口同样不裁剪；权限码已限制可见性
        scope_policy="global",
    ),
    "dicts": ExportSpec(
        key="dicts",
        title="字典",
        permission="system:dict:export",
        header=("ID", "字典名称", "字典编码", "状态", "字典项", "备注", "创建时间"),
        columns=DICT_COLUMNS,
        # 系统全局：字典是全局配置，与列表接口一致
        scope_policy="global",
    ),
    "notices": ExportSpec(
        key="notices",
        title="公告",
        permission="system:notice:export",
        header=("ID", "标题", "置顶", "状态", "发布时间", "创建时间"),
        columns=NOTICE_COLUMNS,
        # 系统全局：公告是全员可见的站内通知，与列表接口一致
        scope_policy="global",
    ),
    "job-logs": ExportSpec(
        key="job-logs",
        title="任务日志",
        permission="schedule:log:export",
        header=("ID", "任务名称", "任务编码", "触发方式", "结果", "耗时(ms)", "错误信息", "执行时间"),
        columns=JOB_LOG_COLUMNS,
        # 系统全局：任务执行日志属于调度系统，与列表接口一致
        scope_policy="global",
    ),
}


async def _user_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    from sqlalchemy.orm import selectinload

    scope = await get_data_scope(db, ctx)
    statement = select(User).options(selectinload(User.dept), selectinload(User.roles))
    statement = apply_user_scope(statement, ctx, scope)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                User.username_normalized.like(like, escape=LIKE_ESCAPE),
                func.lower(User.nickname).like(like, escape=LIKE_ESCAPE),
            )
        )
    if status:
        statement = statement.where(User.status == status)
    return statement.order_by(User.id.desc())


async def _role_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    from sqlalchemy.orm import selectinload

    del db, ctx
    statement = select(Role).options(selectinload(Role.permissions), selectinload(Role.departments))
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(Role.name).like(like, escape=LIKE_ESCAPE),
                Role.code.like(like, escape=LIKE_ESCAPE),
            )
        )
    if status:
        statement = statement.where(Role.status == status)
    return statement.order_by(Role.sort, Role.id)


async def _dict_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    from sqlalchemy.orm import selectinload

    del db, ctx
    statement = select(DictType).options(selectinload(DictType.items))
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                func.lower(DictType.name).like(like, escape=LIKE_ESCAPE),
                DictType.code.like(like, escape=LIKE_ESCAPE),
            )
        )
    if status:
        statement = statement.where(DictType.status == status)
    return statement.order_by(DictType.id.desc())


async def _notice_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    del db, ctx
    statement = select(Notice)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(Notice.title.like(like, escape=LIKE_ESCAPE))
    if status:
        statement = statement.where(Notice.status == status)
    return statement.order_by(Notice.id.desc())


async def _job_log_statement(db: AsyncSession, ctx: AuthContext, keyword: str | None, status: str | None) -> Select:
    del db, ctx
    statement = select(JobLog)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(JobLog.job_name.like(like, escape=LIKE_ESCAPE))
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

        raise AppError("EXPORT_SPEC_UNKNOWN", status_code=404, params={"spec": spec_key})
    statement = await _QUERIES[spec_key](db, ctx, keyword, status)
    # 多取一行即可判断是否被截断，不需要额外 count 全表；
    # 把完整（含探针行）结果交给 csv_response，由它作为唯一截断语义来源
    statement = statement.limit(limit + 1)
    rows: list[Any] = list((await db.execute(statement)).scalars().all())
    response, count, truncated = csv_response(
        spec.header,
        build_rows(rows, spec.columns),
        f"{spec.title}.csv",
        limit=limit,
    )
    # 导出敏感数据必须留痕：谁在什么时候导出了哪一类、多少行
    await _audit_export(db, ctx, spec, count, truncated)
    return response, count, truncated


async def _audit_export(
    db: AsyncSession, ctx: AuthContext, spec: ExportSpec, count: int, truncated: bool
) -> None:
    await write_export_audit(
        db,
        user_id=ctx.user.id,
        username=ctx.user.username,
        module=spec.key.split("-")[0],
        action=f"{spec.key}.export",
        path=f"/export/{spec.key}",
        detail=f"rows={count} truncated={1 if truncated else 0}",
    )
