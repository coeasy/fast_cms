from __future__ import annotations

from fastapi import Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.permissions import ADMIN_PERMISSION
from app.core.authz import AuthContext, get_current_user, require_admin
from app.core.errors import AppError, success
from app.core.pagination import PageParams, page_data
from app.core.routing import protected_router
from app.db.session import get_db
from app.modules.system import service
from app.modules.system.schemas import (
    AssignPermissionsRequest,
    AssignRolesRequest,
    DataScopeUpdate,
    DeptCreate,
    DeptUpdate,
    MenuCreate,
    MenuUpdate,
    ResetPasswordRequest,
    RoleCreate,
    RoleUpdate,
    UserBatchRequest,
    UserCreate,
    UserStatusUpdate,
    UserUpdate,
)

router = protected_router(
    prefix="/system",
    tags=["系统管理"],
    dependencies=[Depends(require_admin)],
)


@router.get("/users", summary="用户列表", permission="system:user:list")
async def list_users(
    params: PageParams = Depends(),
    keyword: str | None = Query(default=None, max_length=64),
    dept_id: int | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(active|disabled)$"),
    role_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await service.list_users(
        db, ctx, params, keyword=keyword, dept_id=dept_id, status=status, role_id=role_id
    )
    return success(page_data(items, total, params))


@router.get("/users/{user_id}", summary="用户详情", permission="system:user:list")
async def get_user(
    user_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_user(db, ctx, user_id))


@router.post("/users", summary="创建用户", permission="system:user:create")
async def create_user(
    data: UserCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_user(db, ctx, data), "用户创建成功")


@router.put("/users/{user_id}", summary="更新用户", permission="system:user:update")
async def update_user(
    user_id: int,
    data: UserUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_user(db, ctx, user_id, data), "用户已更新")


@router.post("/users/batch", summary="批量操作用户", permission="system:user:batch")
async def batch_users(
    data: UserBatchRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await service.batch_users(db, ctx, action=data.action, ids=data.ids)
    ok = len(result["succeeded"])
    failed = len(result["failed"])
    message = f"已处理 {ok} 个用户" + (f"，{failed} 个失败" if failed else "")
    return success(result, message)


@router.post("/users/import", summary="导入用户", permission="system:user:import")
async def import_users(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False, description="只校验不写入，用于导入前预检"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """CSV 导入用户。

    ``dry_run=true`` 时走完整写入路径但不提交，能发现真实导入才会暴露的问题
    （用户名撞车、部门不存在、角色越权），前端可以拿它做「先预检再导入」。
    """
    raw = await file.read()
    if not raw:
        raise AppError("IMPORT_EMPTY", "请选择要导入的 CSV 文件", 422)
    result = await service.import_users(db, ctx, raw, dry_run=dry_run)
    if dry_run:
        message = f"预检完成：{len(result['succeeded'])} 行可导入，{len(result['failed'])} 行有问题"
    else:
        message = f"已导入 {len(result['succeeded'])} 个用户" + (
            f"，{len(result['failed'])} 行失败" if result["failed"] else ""
        )
    return success(result, message)


@router.delete("/users/{user_id}", summary="删除用户", permission="system:user:delete")
async def delete_user(
    user_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_user(db, ctx, user_id)
    return success(None, "用户已删除")


@router.patch("/users/{user_id}/status", summary="启用或禁用用户", permission="system:user:status")
async def set_user_status(
    user_id: int,
    data: UserStatusUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.set_user_status(db, ctx, user_id, data.status), "状态已更新")


@router.post("/users/{user_id}/reset-password", summary="重置用户密码", permission="system:user:reset-password")
async def reset_password(
    user_id: int,
    data: ResetPasswordRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.reset_user_password(db, ctx, user_id, data)
    return success(None, "密码已重置，该用户所有会话已失效")


@router.put("/users/{user_id}/roles", summary="分配用户角色", permission="system:user:assign-role")
async def assign_user_roles(
    user_id: int,
    data: AssignRolesRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.assign_user_roles(db, ctx, user_id, data), "角色已更新")


@router.get("/roles", summary="角色列表", permission="system:role:list")
async def list_roles(
    keyword: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(active|disabled)$"),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_roles(db, keyword=keyword, status=status))


@router.get("/roles/options", summary="角色下拉选项", permission="system:role:list")
async def role_options(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_role_options(db))


@router.get("/roles/{role_id}", summary="角色详情", permission="system:role:list")
async def get_role(
    role_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.get_role(db, role_id))


@router.post("/roles", summary="创建角色", permission="system:role:create")
async def create_role(
    data: RoleCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_role(db, ctx, data), "角色创建成功")


@router.put("/roles/{role_id}", summary="更新角色", permission="system:role:update")
async def update_role(
    role_id: int,
    data: RoleUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_role(db, ctx, role_id, data), "角色已更新")


@router.delete("/roles/{role_id}", summary="删除角色", permission="system:role:delete")
async def delete_role(
    role_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_role(db, ctx, role_id)
    return success(None, "角色已删除")


@router.put("/roles/{role_id}/permissions", summary="分配角色权限", permission="system:role:assign-permission")
async def assign_role_permissions(
    role_id: int,
    data: AssignPermissionsRequest,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.assign_role_permissions(db, ctx, role_id, data), "权限已更新")


@router.put("/roles/{role_id}/data-scope", summary="设置角色数据范围", permission="system:role:data-scope")
async def set_role_data_scope(
    role_id: int,
    data: DataScopeUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.set_role_data_scope(db, ctx, role_id, data), "数据范围已更新")


@router.get("/menus", summary="菜单树", permission="system:menu:list")
async def list_menus(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_menus(db))


@router.post("/menus", summary="创建菜单", permission="system:menu:create")
async def create_menu(
    data: MenuCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_menu(db, ctx, data), "菜单创建成功")


@router.put("/menus/{menu_id}", summary="更新菜单", permission="system:menu:update")
async def update_menu(
    menu_id: int,
    data: MenuUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_menu(db, ctx, menu_id, data), "菜单已更新")


@router.delete("/menus/{menu_id}", summary="删除或停用菜单", permission="system:menu:delete")
async def delete_menu(
    menu_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.delete_menu(db, ctx, menu_id), "菜单已处理")


@router.get("/depts", summary="部门树", permission="system:dept:list")
async def list_depts(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_depts(db, ctx))


@router.get("/depts/options", summary="部门下拉选项", permission=ADMIN_PERMISSION)
async def dept_options(
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_depts(db, ctx))


@router.post("/depts", summary="创建部门", permission="system:dept:create")
async def create_dept(
    data: DeptCreate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.create_dept(db, ctx, data), "部门创建成功")


@router.put("/depts/{dept_id}", summary="更新部门", permission="system:dept:update")
async def update_dept(
    dept_id: int,
    data: DeptUpdate,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.update_dept(db, ctx, dept_id, data), "部门已更新")


@router.delete("/depts/{dept_id}", summary="删除部门", permission="system:dept:delete")
async def delete_dept(
    dept_id: int,
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await service.delete_dept(db, ctx, dept_id)
    return success(None, "部门已删除")


@router.get("/permissions", summary="权限注册表", permission="system:permission:list")
async def list_permissions(
    include_deprecated: bool = Query(default=False),
    ctx: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return success(await service.list_permissions(db, include_deprecated=include_deprecated))
