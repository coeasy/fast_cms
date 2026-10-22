from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import get_cache
from app.core.errors import AppError
from app.core.security import MIN_PASSWORD_LENGTH
from app.core.serializers import iso
from app.core.tz import current_timezone, resolve_timezone, set_timezone
from app.modules.config.models import DictItem, DictType, SystemConfig
from app.modules.config.schemas import (
    ConfigCreate,
    ConfigUpdate,
    DictItemCreate,
    DictItemUpdate,
    DictTypeCreate,
    DictTypeUpdate,
)

PUBLIC_CONFIG_KEY = "fastcms:public-config"
PASSWORD_POLICY_KEY = "fastcms:password-min-length"
TIMEZONE_CONFIG_KEY = "system.timezone"


def dict_cache_key(code: str) -> str:
    return f"fastcms:dict:{code}"


def dict_type_out(item: DictType, *, include_items: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "code": item.code,
        "status": item.status,
        "remark": item.remark,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }
    if include_items:
        data["items"] = [dict_item_out(entry) for entry in sorted(item.items, key=lambda row: (row.sort, row.id))]
    return data


def dict_item_out(item: DictItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "dict_type_id": item.dict_type_id,
        "label": item.label,
        "value": item.value,
        "tag_color": item.tag_color,
        "sort": item.sort,
        "status": item.status,
        "remark": item.remark,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


def config_out(item: SystemConfig) -> dict[str, Any]:
    return {
        "id": item.id,
        "key": item.key,
        "name": item.name,
        "value": item.value,
        "type": item.type,
        "group": item.group,
        "is_public": item.is_public,
        "built_in": item.built_in,
        "remark": item.remark,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
    }


async def list_dict_types(db: AsyncSession, *, keyword: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    statement = select(DictType).options(selectinload(DictType.items))
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(func.lower(DictType.name).like(like) | DictType.code.like(like))
    if status:
        statement = statement.where(DictType.status == status)
    items = (await db.execute(statement.order_by(DictType.id))).scalars().all()
    result = []
    for item in items:
        data = dict_type_out(item)
        data["item_count"] = len(item.items)
        result.append(data)
    return result


async def get_dict_type(db: AsyncSession, type_id: int) -> dict[str, Any]:
    item = (
        (await db.execute(select(DictType).options(selectinload(DictType.items)).where(DictType.id == type_id)))
        .scalars()
        .first()
    )
    if item is None:
        raise AppError("CONFIG_DICT_NOT_FOUND", "字典类型不存在", 404)
    return dict_type_out(item, include_items=True)


async def create_dict_type(db: AsyncSession, data: DictTypeCreate) -> dict[str, Any]:
    exists = (await db.execute(select(DictType.id).where(DictType.code == data.code))).scalar_one_or_none()
    if exists:
        raise AppError("CONFIG_DICT_EXISTS", "字典编码已存在", 409)
    item = DictType(**data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return dict_type_out(item)


async def update_dict_type(db: AsyncSession, type_id: int, data: DictTypeUpdate) -> dict[str, Any]:
    item = await db.get(DictType, type_id)
    if item is None:
        raise AppError("CONFIG_DICT_NOT_FOUND", "字典类型不存在", 404)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await db.commit()
    await get_cache().delete(dict_cache_key(item.code))
    return dict_type_out(item)


async def delete_dict_type(db: AsyncSession, type_id: int) -> None:
    item = await db.get(DictType, type_id)
    if item is None:
        raise AppError("CONFIG_DICT_NOT_FOUND", "字典类型不存在", 404)
    code = item.code
    await db.delete(item)
    await db.commit()
    await get_cache().delete(dict_cache_key(code))


async def list_dict_items(db: AsyncSession, type_id: int) -> list[dict[str, Any]]:
    items = (
        (
            await db.execute(
                select(DictItem).where(DictItem.dict_type_id == type_id).order_by(DictItem.sort, DictItem.id)
            )
        )
        .scalars()
        .all()
    )
    return [dict_item_out(item) for item in items]


async def create_dict_item(db: AsyncSession, type_id: int, data: DictItemCreate) -> dict[str, Any]:
    dict_type = await db.get(DictType, type_id)
    if dict_type is None:
        raise AppError("CONFIG_DICT_NOT_FOUND", "字典类型不存在", 404)
    duplicate = (
        await db.execute(
            select(DictItem.id).where(DictItem.dict_type_id == type_id, DictItem.value == data.value)
        )
    ).scalar_one_or_none()
    if duplicate:
        raise AppError("CONFIG_DICT_ITEM_EXISTS", "同字典下值已存在", 409)
    item = DictItem(dict_type_id=type_id, **data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await get_cache().delete(dict_cache_key(dict_type.code))
    return dict_item_out(item)


async def update_dict_item(db: AsyncSession, item_id: int, data: DictItemUpdate) -> dict[str, Any]:
    item = await db.get(DictItem, item_id)
    if item is None:
        raise AppError("CONFIG_DICT_ITEM_NOT_FOUND", "字典项不存在", 404)
    dict_type = await db.get(DictType, item.dict_type_id)
    payload = data.model_dump(exclude_unset=True)
    if "value" in payload:
        duplicate = (
            await db.execute(
                select(DictItem.id).where(
                    DictItem.dict_type_id == item.dict_type_id,
                    DictItem.value == payload["value"],
                    DictItem.id != item.id,
                )
            )
        ).scalar_one_or_none()
        if duplicate:
            raise AppError("CONFIG_DICT_ITEM_EXISTS", "同字典下值已存在", 409)
    for field, value in payload.items():
        setattr(item, field, value)
    await db.commit()
    if dict_type:
        await get_cache().delete(dict_cache_key(dict_type.code))
    return dict_item_out(item)


async def delete_dict_item(db: AsyncSession, item_id: int) -> None:
    item = await db.get(DictItem, item_id)
    if item is None:
        raise AppError("CONFIG_DICT_ITEM_NOT_FOUND", "字典项不存在", 404)
    dict_type = await db.get(DictType, item.dict_type_id)
    await db.delete(item)
    await db.commit()
    if dict_type:
        await get_cache().delete(dict_cache_key(dict_type.code))


async def read_dict(db: AsyncSession, code: str) -> dict[str, Any]:
    cache = get_cache()
    cached = await cache.get(dict_cache_key(code))
    if cached is not None:
        return cached
    item = (
        (
            await db.execute(
                select(DictType)
                .options(selectinload(DictType.items))
                .where(DictType.code == code, DictType.status == "active")
            )
        )
        .scalars()
        .first()
    )
    if item is None:
        raise AppError("CONFIG_DICT_NOT_FOUND", "字典不存在或已停用", 404)
    data = {
        "code": item.code,
        "name": item.name,
        "items": [
            {"label": entry.label, "value": entry.value, "tag_color": entry.tag_color}
            for entry in sorted(item.items, key=lambda row: (row.sort, row.id))
            if entry.status == "active"
        ],
    }
    await cache.set(dict_cache_key(code), data, ttl=300)
    return data


def _parse_config_value(value: str, value_type: str) -> Any:
    if value_type == "bool":
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value_type == "int":
        try:
            return int(value)
        except ValueError:
            return 0
    if value_type == "json":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _check_config_value(value: str, value_type: str) -> None:
    """按声明类型校验参数值，避免保存后 public-config 静默把非法值解析成 0/None。"""
    if value_type == "int":
        try:
            int(value.strip())
        except ValueError:
            raise AppError("CONFIG_VALUE_INVALID", f"参数值必须是整数，当前为：{value!r}", 422)
    elif value_type == "json":
        try:
            json.loads(value)
        except json.JSONDecodeError as error:
            raise AppError("CONFIG_VALUE_INVALID", f"参数值必须是合法 JSON：{error.msg}", 422)


async def public_config(db: AsyncSession) -> dict[str, Any]:
    cache = get_cache()
    cached = await cache.get(PUBLIC_CONFIG_KEY)
    if cached is not None:
        return cached
    configs = (
        (await db.execute(select(SystemConfig).where(SystemConfig.is_public.is_(True)))).scalars().all()
    )
    data = {item.key: _parse_config_value(item.value, item.type) for item in configs}
    data.setdefault("system.title", "Fast CMS")
    data.setdefault("system.short_title", "Fast CMS")
    data.setdefault("system.copyright", "Fast CMS")
    # 老库没有这条参数时也不能让前端按浏览器时区瞎猜，回落到启动时解析好的系统时区
    data["system.timezone"] = data.get("system.timezone") or current_timezone()
    min_length = await password_min_length(db)
    data["auth.password_min_length"] = min_length
    data["auth.password_rule"] = f"至少 {min_length} 位，必须同时包含字母和数字，且不能包含用户名"
    await cache.set(PUBLIC_CONFIG_KEY, data, ttl=300)
    return data


async def password_min_length(db: AsyncSession) -> int:
    """密码最小长度（`security.password_min_length` 可调，默认与下限均为 8）。"""
    cache = get_cache()
    cached = await cache.get(PASSWORD_POLICY_KEY)
    if isinstance(cached, int) and cached >= MIN_PASSWORD_LENGTH:
        return cached
    item = (
        await db.execute(select(SystemConfig).where(SystemConfig.key == "security.password_min_length"))
    ).scalars().first()
    value = MIN_PASSWORD_LENGTH
    if item is not None:
        parsed = _parse_config_value(item.value, item.type)
        # 钳制到实际强制下限：配置填 3 时服务端仍按 8 位校验，缓存与对外规则文案必须报同一个值
        if isinstance(parsed, int) and parsed > 0:
            value = max(MIN_PASSWORD_LENGTH, min(parsed, 64))
    await cache.set(PASSWORD_POLICY_KEY, value, ttl=300)
    return value


async def _invalidate_config_cache() -> None:
    cache = get_cache()
    await cache.delete(PUBLIC_CONFIG_KEY)
    await cache.delete(PASSWORD_POLICY_KEY)


def _check_timezone(key: str, value: str) -> None:
    if key != TIMEZONE_CONFIG_KEY:
        return
    try:
        resolve_timezone(value)
    except ValueError as error:
        raise AppError("CONFIG_TIMEZONE_INVALID", error.args[0], 422) from error


async def apply_timezone(zone_name: str) -> None:
    """把 `system.timezone` 应用到运行期：切时区并让调度器按新时区重排任务。"""
    resolved = resolve_timezone(zone_name)
    if resolved == current_timezone():
        return
    set_timezone(resolved)
    # 延迟导入：调度器模块会反过来依赖任务注册表，启动顺序上不能提前引用
    from app.modules.schedule.scheduler import scheduler_service

    await scheduler_service.resync_timezone()


async def load_timezone_from_db(db: AsyncSession) -> str:
    """进程启动时把数据库里的系统时区读回运行期，避免设置只活到下次重启。"""
    item = (await db.execute(select(SystemConfig).where(SystemConfig.key == TIMEZONE_CONFIG_KEY))).scalars().first()
    if item is None:
        return current_timezone()
    try:
        return set_timezone(item.value)
    except ValueError:
        # 库里存了非法时区名（多为直接改库）时保持环境变量初值，不能让应用起不来
        return current_timezone()


async def list_configs(db: AsyncSession, *, keyword: str | None = None, group: str | None = None) -> list[dict[str, Any]]:
    statement = select(SystemConfig)
    if keyword:
        like = f"%{keyword.strip().lower()}%"
        statement = statement.where(
            func.lower(SystemConfig.name).like(like) | SystemConfig.key.like(like)
        )
    if group:
        statement = statement.where(SystemConfig.group == group)
    items = (await db.execute(statement.order_by(SystemConfig.group, SystemConfig.key))).scalars().all()
    return [config_out(item) for item in items]


async def create_config(db: AsyncSession, data: ConfigCreate) -> dict[str, Any]:
    exists = (await db.execute(select(SystemConfig.id).where(SystemConfig.key == data.key))).scalar_one_or_none()
    if exists:
        raise AppError("CONFIG_KEY_EXISTS", "参数键已存在", 409)
    _check_config_value(data.value, data.type)
    _check_timezone(data.key, data.value)
    item = SystemConfig(**data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await _invalidate_config_cache()
    if item.key == TIMEZONE_CONFIG_KEY:
        await apply_timezone(item.value)
    return config_out(item)


async def update_config(db: AsyncSession, config_id: int, data: ConfigUpdate) -> dict[str, Any]:
    item = await db.get(SystemConfig, config_id)
    if item is None:
        raise AppError("CONFIG_NOT_FOUND", "系统参数不存在", 404)
    payload = data.model_dump(exclude_unset=True)
    _check_config_value(payload.get("value", item.value), payload.get("type", item.type))
    for field, value in payload.items():
        setattr(item, field, value)
    _check_timezone(item.key, item.value)
    await db.commit()
    await _invalidate_config_cache()
    if item.key == TIMEZONE_CONFIG_KEY:
        await apply_timezone(item.value)
    return config_out(item)


async def delete_config(db: AsyncSession, config_id: int) -> None:
    item = await db.get(SystemConfig, config_id)
    if item is None:
        raise AppError("CONFIG_NOT_FOUND", "系统参数不存在", 404)
    if item.built_in:
        raise AppError("CONFIG_BUILTIN", "内置参数不可删除", 409)
    await db.delete(item)
    await db.commit()
    await _invalidate_config_cache()
