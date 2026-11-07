from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import ipgeo
from app.core.authz import AuthContext, apply_user_scope
from app.core.csvio import optional_int, optional_text, read_rows, split_ids
from app.core.errors import AppError
from app.core.pagination import PageParams, paginate
from app.core.search import LIKE_ESCAPE, contains
from app.core.security import hash_password_async, validate_password_strength
from app.kernel.db import commit, transactional
from app.kernel.models import Dept, Role, User, user_roles
from app.modules.sys.config.service import password_min_length
from app.modules.sys.system.schemas import AssignRolesRequest, ResetPasswordRequest, UserCreate, UserUpdate
from app.modules.sys.system.services._common import (
    USERNAME_ADMIN,
    _ensure_can_manage_superuser,
    _ensure_user_access,
    _log_operation,
    _normalize_username,
    _revoke_sessions,
    _user_payload,
    _user_scope,
)


async def _ensure_new_user_scope(db: AsyncSession, ctx: AuthContext, dept_id: int | None) -> None:
    if ctx.is_superuser:
        return
    scope = await _user_scope(db, ctx)
    if scope.all_access:
        return
    if not dept_id or dept_id not in scope.dept_ids:
        raise AppError("SYSTEM_USER_DEPT_FORBIDDEN", status_code=403)


async def _last_superuser_guard(db: AsyncSession, user_id: int) -> None:
    total = int(
        (
            await db.execute(
                select(func.count(User.id)).where(User.is_superuser.is_(True), User.id != user_id)
            )
        ).scalar()
        or 0
    )
    if total == 0:
        raise AppError("SYSTEM_LAST_SUPERUSER", status_code=409)


async def _load_roles_for_assignment(
    db: AsyncSession, ctx: AuthContext, role_ids: list[int]
) -> list[Role]:
    if not role_ids:
        return []
    roles = (
        (
            await db.execute(
                select(Role)
                .options(selectinload(Role.permissions))
                .where(Role.id.in_(set(role_ids)))
            )
        )
        .scalars()
        .all()
    )
    if len(roles) != len(set(role_ids)):
        raise AppError("SYSTEM_ROLE_NOT_FOUND", "部分角色不存在", 404)
    if ctx.is_superuser:
        return list(roles)
    scope = await _user_scope(db, ctx)
    allowed_scope = {"self", "dept"} if not scope.all_access else {"self", "dept", "dept_and_below", "custom", "all"}
    for role in roles:
        if role.status != "active":
            raise AppError("SYSTEM_ROLE_DISABLED", status_code=409, params={"role": role.name})
        if role.data_scope not in allowed_scope:
            raise AppError("SYSTEM_ROLE_ESCALATION", "不能分配数据范围超过你的角色", 403)
        codes = {item.code for item in role.permissions if item.deprecated_at is None}
        if not codes.issubset(ctx.permissions):
            raise AppError("SYSTEM_ROLE_ESCALATION", f"不能分配权限超过你的角色「{role.name}」", 403)
    return list(roles)


async def list_users(
    db: AsyncSession,
    ctx: AuthContext,
    params: PageParams,
    *,
    keyword: str | None = None,
    dept_id: int | None = None,
    status: str | None = None,
    role_id: int | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    scope = await _user_scope(db, ctx)
    statement = select(User).options(selectinload(User.dept), selectinload(User.roles))
    statement = apply_user_scope(statement, ctx, scope)
    like = contains(keyword)
    if like is not None:
        statement = statement.where(
            or_(
                User.username_normalized.like(like, escape=LIKE_ESCAPE),
                func.lower(User.nickname).like(like, escape=LIKE_ESCAPE),
                User.phone.like(like, escape=LIKE_ESCAPE),
                func.lower(User.email).like(like, escape=LIKE_ESCAPE),
            )
        )
    if dept_id:
        dept = await db.get(Dept, dept_id)
        if dept is None:
            raise AppError("SYSTEM_DEPT_NOT_FOUND", "部门不存在", 404)
        # 前缀匹配：ancestors 形如 "/1/2/"，子树路径前缀为 "/1/2/<id>/"。
        # 用前缀取代 "%/<id>/%" 避免前置通配绕过索引（P1-7）。
        dept_path = f"{dept.ancestors}{dept.id}/"
        subtree = (
            (
                await db.execute(
                    select(Dept.id).where(
                        or_(Dept.id == dept_id, Dept.ancestors.like(f"{dept_path}%"))
                    )
                )
            )
            .scalars()
            .all()
        )
        if not scope.all_access:
            subtree = [item for item in subtree if item in scope.dept_ids]
        statement = statement.where(User.dept_id.in_(subtree))
    if status:
        statement = statement.where(User.status == status)
    if role_id:
        statement = statement.where(User.id.in_(select(user_roles.c.user_id).where(user_roles.c.role_id == role_id)))
    statement = statement.order_by(User.id.desc())
    users, total = await paginate(db, statement, params)
    locations = await ipgeo.locate_many(item.last_login_ip for item in users)
    return [_user_payload(ctx, item, locations) for item in users], total


async def get_user(db: AsyncSession, ctx: AuthContext, user_id: int) -> dict[str, Any]:
    user = (
        (
            await db.execute(
                select(User)
                .options(selectinload(User.dept), selectinload(User.roles))
                .where(User.id == user_id)
            )
        )
        .scalars()
        .first()
    )
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    locations = await ipgeo.locate_many([user.last_login_ip])
    return _user_payload(ctx, user, locations)


async def _create_user(db: AsyncSession, ctx: AuthContext, data: UserCreate) -> User:
    """校验并写入用户，**不提交**，返回重新加载过的实体。

    拆出这一层是为了让「单个创建」与「批量导入」共用同一套校验（用户名查重、
    密码强度、部门与角色越权、超管权限），同时让导入能给每一行套一个 SAVEPOINT：
    第 37 行失败只回滚这一行，前 36 行仍然保留。

    此前这个能力靠 ``create_user(commit=False)`` 这个布尔开关表达——开关的问题是
    它同时承担「不提交」和「预检」两种含义，调用方很难判断该传什么。改成
    「内核不提交、由调用方决定事务边界」之后，开关就消失了。
    """
    username = data.username.strip()
    normalized = _normalize_username(username)
    exists = (await db.execute(select(User.id).where(User.username_normalized == normalized))).scalar_one_or_none()
    if exists:
        raise AppError("SYSTEM_USERNAME_EXISTS", status_code=409)
    valid, message = validate_password_strength(
        data.password, username, min_length=await password_min_length(db)
    )
    if not valid:
        raise AppError("SYSTEM_WEAK_PASSWORD", message, 422)
    if data.is_superuser and not ctx.is_superuser:
        raise AppError("SYSTEM_USER_FORBIDDEN", "仅超级管理员可创建超级管理员", 403)
    await _ensure_new_user_scope(db, ctx, data.dept_id)
    if data.dept_id:
        dept = await db.get(Dept, data.dept_id)
        if dept is None:
            raise AppError("SYSTEM_DEPT_NOT_FOUND", "部门不存在", 404)
    roles = await _load_roles_for_assignment(db, ctx, data.role_ids)
    user = User(
        username=username,
        username_normalized=normalized,
        password_hash=await hash_password_async(data.password),
        nickname=data.nickname.strip(),
        email=data.email,
        phone=data.phone,
        dept_id=data.dept_id,
        status=data.status,
        is_superuser=data.is_superuser,
        must_change_password=data.must_change_password or data.is_superuser,
        remark=data.remark,
    )
    user.roles = roles
    db.add(user)
    await _log_operation(db, ctx, module="system", action="user.create", detail=f"username={username}", path="/system/users")
    # 必须 flush 才能拿到自增主键、触发唯一约束；把 IntegrityError 就地翻译成
    # 语义化错误，否则导入时一行撞唯一索引会以原始异常冒到 500。
    try:
        await db.flush()
    except IntegrityError as exc:
        raise AppError("DATA_CONFLICT") from exc
    created = (
        (
            await db.execute(
                select(User)
                .options(selectinload(User.dept), selectinload(User.roles))
                .where(User.id == user.id)
            )
        )
        .scalars()
        .first()
    )
    assert created is not None
    return created


async def create_user(db: AsyncSession, ctx: AuthContext, data: UserCreate) -> dict[str, Any]:
    """创建单个用户：写入 + 提交。"""
    created = await _create_user(db, ctx, data)
    await commit(db)
    locations = await ipgeo.locate_many([created.last_login_ip])
    return _user_payload(ctx, created, locations)


async def update_user(db: AsyncSession, ctx: AuthContext, user_id: int, data: UserUpdate) -> dict[str, Any]:
    user = (
        (await db.execute(select(User).options(selectinload(User.roles), selectinload(User.dept)).where(User.id == user_id)))
        .scalars()
        .first()
    )
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    payload = data.model_dump(exclude_unset=True)
    role_ids = payload.pop("role_ids", None)
    if "dept_id" in payload and payload["dept_id"] != user.dept_id:
        dept_id = payload["dept_id"]
        await _ensure_new_user_scope(db, ctx, dept_id)
        if dept_id is not None:
            dept = await db.get(Dept, dept_id)
            if dept is None:
                raise AppError("SYSTEM_DEPT_NOT_FOUND", "部门不存在", 404)
    if payload.get("is_superuser") is not None:
        if not ctx.is_superuser:
            raise AppError("SYSTEM_USER_FORBIDDEN", "仅超级管理员可调整超级管理员标记", 403)
        if user.id == ctx.user.id and not payload["is_superuser"]:
            raise AppError("SYSTEM_SELF_DEMOTE", status_code=409)
        if not payload["is_superuser"] and user.is_superuser:
            await _last_superuser_guard(db, user.id)
    if payload.get("status") == "disabled":
        if user.id == ctx.user.id:
            raise AppError("SYSTEM_SELF_DISABLE", status_code=409)
        await _revoke_sessions(db, user.id)
        await db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    for field in ("nickname", "dept_id", "email", "phone", "status", "is_superuser", "remark"):
        if field in payload:
            setattr(user, field, payload[field])
    if role_ids is not None:
        if not ctx.has("system:user:assign-role"):
            raise AppError("SYSTEM_USER_FORBIDDEN", "缺少分配角色权限，请改用角色接口", 403)
        user.roles = await _load_roles_for_assignment(db, ctx, role_ids)
    await _log_operation(db, ctx, module="system", action="user.update", detail=f"user_id={user.id}", method="PUT", path=f"/system/users/{user.id}")
    await commit(db)
    locations = await ipgeo.locate_many([user.last_login_ip])
    return _user_payload(ctx, user, locations)


async def set_user_status(db: AsyncSession, ctx: AuthContext, user_id: int, status: str) -> dict[str, Any]:
    return await update_user(db, ctx, user_id, UserUpdate(status=status))  # type: ignore[arg-type]


async def delete_user(db: AsyncSession, ctx: AuthContext, user_id: int) -> None:
    """删除用户：吊销会话 + 写审计 + 删行，三件事一次提交。

    这里用显式 ``commit`` 而不是 ``@transactional``：本函数被 ``batch_users`` 逐条
    调用，装饰器的整事务回滚会让 session 里所有对象过期，循环里再读 ``ctx.user``
    就会 ``MissingGreenlet``。批量语义要的是「单条失败不影响其它条」，见
    ``kernel/db.py::transactional`` 的边界说明。
    """
    user = await db.get(User, user_id)
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    if user.id == ctx.user.id:
        raise AppError("SYSTEM_SELF_DELETE", status_code=409)
    if user.username_normalized == USERNAME_ADMIN:
        raise AppError("SYSTEM_BUILTIN_USER", status_code=409)
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    if user.is_superuser:
        await _last_superuser_guard(db, user.id)
    await _revoke_sessions(db, user.id)
    await _log_operation(db, ctx, module="system", action="user.delete", detail=f"user_id={user.id}", method="DELETE", path=f"/system/users/{user.id}")
    await db.delete(user)
    await commit(db)


BATCH_ACTIONS = ("enable", "disable", "delete")


MAX_BATCH_IDS = 200


async def batch_users(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    action: str,
    ids: list[int],
) -> dict[str, Any]:
    """批量启停 / 删除用户。

    单条失败不影响其他条目：把成功与失败的 id 分别回传，前端按结果提示，
    而不是整个事务回滚后让用户从头再来。
    """
    if action not in BATCH_ACTIONS:
        raise AppError("SYSTEM_BATCH_ACTION_INVALID", status_code=422, params={"action": action})
    if not ids:
        raise AppError("SYSTEM_BATCH_EMPTY", status_code=422)
    if len(ids) > MAX_BATCH_IDS:
        raise AppError("SYSTEM_BATCH_TOO_MANY", status_code=422, params={"limit": MAX_BATCH_IDS})

    succeeded: list[int] = []
    failed: list[dict[str, Any]] = []
    for user_id in dict.fromkeys(ids):
        try:
            if action == "delete":
                await delete_user(db, ctx, user_id)
            elif action == "disable":
                await update_user(db, ctx, user_id, UserUpdate(status="disabled"))
            else:
                await update_user(db, ctx, user_id, UserUpdate(status="active"))
            succeeded.append(user_id)
        except AppError as exc:
            failed.append({"id": user_id, "code": exc.code, "message": exc.message})

    await _log_operation(
        db,
        ctx,
        module="system",
        action="user.batch",
        detail=f"action={action} total={len(ids)} ok={len(succeeded)} failed={len(failed)}",
        method="POST",
        path="/system/users/batch",
    )
    await commit(db)
    return {"action": action, "succeeded": succeeded, "failed": failed}


@transactional
async def reset_user_password(
    db: AsyncSession, ctx: AuthContext, user_id: int, data: ResetPasswordRequest
) -> None:
    """管理员重置密码：改 hash + 清锁定 + 提版本号 + 吊销会话 + 写审计。

    这五步必须一起成立——只改了 hash 没提 ``token_version``，旧会话还能继续用；
    只吊销了会话没写审计，事后无法追溯是谁做的。所以交给 ``@transactional``。
    """
    user = await db.get(User, user_id)
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    valid, message = validate_password_strength(
        data.new_password, user.username, min_length=await password_min_length(db)
    )
    if not valid:
        raise AppError("SYSTEM_WEAK_PASSWORD", message, 422)
    user.password_hash = await hash_password_async(data.new_password)
    user.must_change_password = data.must_change_password
    # 重置密码即视为管理员介入：顺手清掉失败计数与锁定截止时间，否则锁定中的账号只能干等窗口过去
    user.failed_login_count = 0
    user.locked_until = None
    user.token_version += 1
    await _revoke_sessions(db, user.id)
    await _log_operation(db, ctx, module="system", action="user.reset-password", detail=f"user_id={user.id}", path=f"/system/users/{user.id}/reset-password")


@transactional
async def assign_user_roles(
    db: AsyncSession, ctx: AuthContext, user_id: int, data: AssignRolesRequest
) -> dict[str, Any]:
    """整体替换用户角色：要么全换成新的，要么保持原样。

    角色集合是「覆盖写」，中途失败留下半套角色比失败本身更危险——权限边界
    会停在一个谁也没设计过的状态。所以整段包在一个事务里。
    """
    user = (
        (await db.execute(select(User).options(selectinload(User.roles), selectinload(User.dept)).where(User.id == user_id)))
        .scalars()
        .first()
    )
    await _ensure_user_access(db, ctx, user)
    assert user is not None
    _ensure_can_manage_superuser(ctx, user.is_superuser)
    roles = await _load_roles_for_assignment(db, ctx, data.role_ids)
    user.roles = roles
    await _log_operation(db, ctx, module="system", action="user.assign-role", detail=f"user_id={user.id};roles={data.role_ids}", method="PUT", path=f"/system/users/{user.id}/roles")
    locations = await ipgeo.locate_many([user.last_login_ip])
    return _user_payload(ctx, user, locations)


USER_IMPORT_COLUMNS: dict[str, str] = {
    "username": "username",
    "用户名": "username",
    "password": "password",
    "密码": "password",
    "nickname": "nickname",
    "昵称": "nickname",
    "dept_id": "dept_id",
    "部门id": "dept_id",
    "email": "email",
    "邮箱": "email",
    "phone": "phone",
    "手机号": "phone",
    "status": "status",
    "状态": "status",
    "role_ids": "role_ids",
    "角色id": "role_ids",
    "remark": "remark",
    "备注": "remark",
    "must_change_password": "must_change_password",
    "首次登录改密": "must_change_password",
}


USER_IMPORT_REQUIRED: tuple[str, ...] = ("username", "password", "nickname")


def _user_create_from_row(row: dict[str, str]) -> UserCreate:
    """把一行 CSV 转成 ``UserCreate``；字段级错误在这里就变成行号可定位的失败。"""
    payload: dict[str, Any] = {
        "username": row.get("username", ""),
        "password": row.get("password", ""),
        "nickname": row.get("nickname", ""),
        "dept_id": optional_int(row.get("dept_id")),
        "email": optional_text(row.get("email")),
        "phone": optional_text(row.get("phone")),
        "status": row.get("status") or "active",
        "role_ids": split_ids(row.get("role_ids")),
        "remark": optional_text(row.get("remark")),
        "must_change_password": (row.get("must_change_password") or "").lower()
        not in ("false", "0", "否", "no"),
    }
    try:
        return UserCreate(**payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ()))
        label = field or "整行"
        raise AppError("IMPORT_ROW_INVALID", f"字段 {label}：{first.get('msg', '格式不正确')}", 422) from exc


async def import_users(
    db: AsyncSession,
    ctx: AuthContext,
    raw: bytes,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """批量导入用户。

    三条设计取舍：

    - **逐行调用 ``_create_user``** 而不是自己再实现一遍校验。用户名查重、
      密码强度、部门与角色越权、超级管理员权限全部复用同一套逻辑，
      不会出现「导入放过的用户，单个创建时反而被拒」这种割裂。
    - **单行失败不回滚整批**：第 37 行密码太弱不该让前 36 行白导入。
      每行独立提交，失败行的行号、用户名、原因都原样返回，用户改完 CSV 可以只重传。
      这里不用 SAVEPOINT：pysqlite 在「SAVEPOINT 是事务里第一条语句」时会把
      ``RELEASE SAVEPOINT`` 退化成提交，局部回滚不成立（见 docs 的「环境陷阱」）。
    - **整批不套 ``@transactional``**：那是「全成功或全失败」的语义，与这里的
      「部分成功」正好相反。

    ``dry_run`` 走完整写入路径（含 flush，因此唯一约束、外键、部门越权都会真实
    触发）但不提交，最后统一 ``rollback``，能发现只做「纸上校验」时发现不了的问题。
    """
    rows = read_rows(
        raw, columns=USER_IMPORT_COLUMNS, required=USER_IMPORT_REQUIRED
    )
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    try:
        for offset, row in enumerate(rows):
            # 表头占第 1 行，数据从第 2 行开始，报行号时按 CSV 里的真实行号算
            line = offset + 2
            username = row.get("username", "")
            try:
                data = _user_create_from_row(row)
            except AppError as exc:
                failed.append({"row": line, "username": username, "code": exc.code, "message": exc.message})
                continue
            try:
                created = await _create_user(db, ctx, data)
                if not dry_run:
                    await commit(db)
            except AppError as exc:
                # 失败行的 session 可能停在半完成状态，先回滚再继续，
                # 否则下一行会踩在上一个失败事务的残留上
                await db.rollback()
                failed.append({"row": line, "username": data.username, "code": exc.code, "message": exc.message})
                continue
            succeeded.append({"row": line, "username": created.username, "id": created.id})

        if dry_run:
            await db.rollback()
        else:
            await _log_operation(
                db,
                ctx,
                module="system",
                action="user.import",
                detail=f"total={len(rows)} ok={len(succeeded)} failed={len(failed)}",
                path="/system/users/import",
            )
            await commit(db)
    except BaseException:
        # 循环之外的意外必须回滚，别把半批数据留在会话里
        await db.rollback()
        raise

    return {
        "total": len(rows),
        "succeeded": succeeded,
        "failed": failed,
        "dry_run": dry_run,
    }
