"""B-11 / 4-4：并发竞态专项（asyncio.gather）。

针对两个真实竞态路径补并发测试：

1. **刷新令牌轮换**：``refresh_session`` 用「条件 UPDATE」做轮换锁，并发刷新同一
   凭证时只有一方能命中，其余按无效/复用处理，绝不互相顶掉 —— 恰好一个成功。
2. **唯一约束冲突转 409**：``kernel.db.commit()`` 把 ``IntegrityError`` 翻译成
   ``DATA_CONFLICT``（409）。并发插入相同唯一键时，恰好一个成功，其余全部 409，
   不会出现「静默覆盖」或「两笔都成功」。

SQLite 是单写者，但 WAL + busy_timeout 允许并发连接串行写；这里断言的是
**业务不变量**（恰好一个赢家），而不是计时，因此不受写锁竞争影响。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

import app.db.session as session_mod
import app.kernel.db as kernel_db
from app.core.errors import AppError
from app.db.session import session_scope
from app.kernel.models import Role
from app.modules.sys.auth import service as auth_service

CONCURRENCY = 8
# admin 口令在模板库中已固化为 Owner123456（见 conftest._build_template）
ADMIN_PASSWORD = "Owner123456"


async def _login_once() -> str:
    """登录一次拿到有效刷新凭证（每个测试各自新建会话，避免跨测试耦合）。"""
    async with session_scope() as db:
        result = await auth_service.login(
            db,
            username="admin",
            password=ADMIN_PASSWORD,
            ip="203.0.113.10",
            user_agent="concurrency-test",
        )
    return result["refresh_token"]


async def _refresh_once(refresh_token: str):
    """在独立会话里刷新一次；成功返回 dict，失败返回 AppError。

    会话工厂必须经 ``session_mod.async_session_factory`` 在调用时解析——每个测试
    的 client 夹具会把该模块属性重绑到独立副本引擎，import 时固化的工厂会指向
    模板库，导致刷新打到错误库上。
    """
    async with session_mod.async_session_factory() as db:
        try:
            return await auth_service.refresh_session(
                db,
                refresh_token=refresh_token,
                ip="203.0.113.10",
                user_agent="concurrency-test",
            )
        except AppError as exc:
            return exc


async def _insert_role_once(code: str):
    """在独立会话里插入一个同 code 角色并提交；成功返回 "ok"，冲突返回 AppError。"""
    async with session_mod.async_session_factory() as db:
        db.add(Role(name="并发角色", code=code, data_scope="all"))
        try:
            await kernel_db.commit(db)
            return "ok"
        except AppError as exc:
            return exc


async def test_concurrent_refresh_keeps_single_rotation(client: TestClient) -> None:
    """并发刷新同一凭证：只有一方真正轮换，其余走宽限补发 access token。

    轮换锁是 ``UPDATE ... WHERE refresh_token_hash = :old AND revoked_at IS NULL``，
    并发时只有第一个事务能把它改成新哈希。其余请求拿着的是「上一代」token，
    在宽限窗口内按并发处理——补发 access token、不再轮换、不撤销会话。
    如果把它们判成重放，用户开两个标签页就会被互相踢下线。
    """
    refresh_token = await _login_once()
    results = await asyncio.gather(*[_refresh_once(refresh_token) for _ in range(CONCURRENCY)])

    failures = [r for r in results if isinstance(r, AppError)]
    assert not failures, f"并发刷新不应失败：{[f.code for f in failures]}"
    rotated = [r for r in results if r.get("refresh_token")]
    assert len(rotated) == 1, f"应恰好 1 个请求轮换凭证，实际 {len(rotated)}"
    for result in results:
        assert result["access_token"], "每个并发刷新都应拿到可用的 access token"


async def test_unique_conflict_concurrency_yields_one_winner(client: TestClient) -> None:
    """并发插入相同唯一键：恰好一个提交成功，其余全部 DATA_CONFLICT(409)。

    验证 ``IntegrityError`` 不会静默吞掉或让两笔都成功 —— 唯一约束就是最后防线，
    冲突必须显式转成 409，前端才能提示用户「数据重复」。
    """
    code = f"concurrent_role_{id(client)}"
    results = await asyncio.gather(*[_insert_role_once(code) for _ in range(CONCURRENCY)])

    oks = [r for r in results if r == "ok"]
    errors = [r for r in results if isinstance(r, AppError)]

    assert len(oks) == 1, f"并发唯一键插入应恰好 1 个成功，实际 {len(oks)}"
    assert len(errors) == CONCURRENCY - 1
    for err in errors:
        assert err.code == "DATA_CONFLICT"
        assert err.status_code == 409

    # 落库的确实是那一行，且只有一行
    async with session_scope() as db:
        rows = (
            (await db.execute(select(Role).where(Role.code == code)))
            .scalars()
            .all()
        )
    assert len(rows) == 1, f"唯一键冲突后应只落 1 行，实际 {len(rows)}"


async def test_commit_translates_integrity_error_to_conflict(client: TestClient) -> None:
    """``kernel.db.commit`` 对重复唯一键的翻译语义（非并发基准路径）。

    单独钉死翻译本身：直接插入两个相同 code，第二个提交必须抛 DATA_CONFLICT(409)，
    而不是让原始 IntegrityError 冒出去（那会落到全局 handler 变成笼统 500）。
    """
    code = f"conflict_semantics_{id(client)}"
    async with session_mod.async_session_factory() as db:
        db.add(Role(name="角色甲", code=code, data_scope="all"))
        await kernel_db.commit(db)

    async with session_mod.async_session_factory() as db:
        db.add(Role(name="角色乙", code=code, data_scope="all"))
        try:
            await kernel_db.commit(db)
        except AppError as err:
            assert err.code == "DATA_CONFLICT", f"应转 DATA_CONFLICT，实际 {err.code}"
            assert err.status_code == 409, f"应 409，实际 {err.status_code}"
        else:
            raise AssertionError("重复唯一键提交应抛 DATA_CONFLICT，实际成功")
