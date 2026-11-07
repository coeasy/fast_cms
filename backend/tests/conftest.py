"""P0-8：测试隔离 —— 每个测试使用独立临时库，禁止直接修改共享库。

设计要点：
- 会话开始时构建一份「模板库」：由应用 lifespan 完成迁移 + 种子，并把管理员口令
  固化为 Owner123456，使每个测试副本都从「已改密、已就绪」状态开始，避免测试间
  通过共享库发生顺序耦合（如密码状态、残留行、demo 业务表）。
- 每个测试通过 ``client`` 夹具拿到一份模板库的副本（文件复制），并把全局引擎、
  会话工厂与 ``settings.db_url`` 重新指向该副本；测试内再创建 ``TestClient(create_app())``
  也走同一全局引擎，自然落在同一副本上。
- 模板库本身绝不回写：测试只能在各自的副本里建 demo 表、增删数据，schema drift
  校验因此不受任何业务残留影响。
- 需要直接读库的测试用 ``current_db_path()`` 取当前副本路径，而不是引用共享库常量。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-perf", action="store_true", default=False, help="运行性能基线测试（tests/perf）")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "perf: 性能基线测试，默认跳过，需 --run-perf")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-perf"):
        return
    skip = pytest.mark.skip(reason="性能基线需要 --run-perf")
    for item in items:
        if "perf" in item.keywords:
            item.add_marker(skip)

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATA = BACKEND_DIR / "data"
# 模板库路径带进程号：多 worker / 并行 CI 各自构建自己的模板，避免同路径竞争。
# 该路径仅用于「构建模板」这一会话动作；每个测试用 tmp_path 拿到独立副本。
TEMPLATE_DB = TEST_DATA / f"test_fastcms_template_{os.getpid()}.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMPLATE_DB) + suffix).unlink(missing_ok=True)

os.environ["DB_URL"] = f"sqlite+aiosqlite:///{TEMPLATE_DB.as_posix()}"
os.environ["INITIAL_ADMIN_PASSWORD"] = "RootInit12345"
os.environ["APP_ENV"] = "test"
os.environ["SCHEDULER_MODE"] = "disabled"
os.environ["APP_CORS_ORIGINS"] = "http://localhost:8000"
# 测试用通知渠道指向本地地址，放开内网校验才能覆盖完整发送链路
os.environ["NOTIFY_ALLOW_PRIVATE_TARGET"] = "true"
# P1-12：Python 脚本任务默认关闭，测试显式开启以覆盖完整链路
os.environ["SCHEDULER_PYTHON_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

import app.codegen.router as codegen_mod  # noqa: E402
import app.db.migrate as migrate_mod  # noqa: E402
import app.db.session as session_mod  # noqa: E402
import app.health as health_mod  # noqa: E402
import app.kernel.ipguard as ip_mod  # noqa: E402
import app.kernel.middleware as mw_mod  # noqa: E402
import app.main as main_mod  # noqa: E402
import app.startup as startup_mod  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session", autouse=True)
def _build_template() -> Iterator[None]:
    """一次性构建迁移 + 种子齐全、管理员口令已固化的模板库。"""
    with TestClient(app) as template_client:
        data = _login(template_client, "admin", os.environ["INITIAL_ADMIN_PASSWORD"])
        changed = template_client.post(
            "/api/v1/auth/change-password",
            headers=_auth(data["access_token"]),
            json={"old_password": os.environ["INITIAL_ADMIN_PASSWORD"], "new_password": "Owner123456"},
        )
        assert changed.status_code == 200, changed.text
    # lifespan 退出时引擎已 dispose、WAL 已合并回主文件；清掉残留 -wal/-shm
    for suffix in ("-wal", "-shm"):
        Path(str(TEMPLATE_DB) + suffix).unlink(missing_ok=True)
    yield
    for suffix in ("", "-wal", "-shm"):
        Path(str(TEMPLATE_DB) + suffix).unlink(missing_ok=True)


# 当前测试的数据库副本路径；仅在有 client 的测试内有效。
_current_test_db: Path | None = None


def current_db_path() -> Path:
    """返回当前测试的独立数据库副本路径，供需要直连读库的测试使用。"""
    assert _current_test_db is not None, "current_db_path() 只能在依赖 client 的测试里调用"
    return _current_test_db


def _repoint_engine(db_url: str):
    """重建全局引擎与会话工厂，并让所有直接引用它们的模块指向新引擎。

    ``get_db`` / ``session_scope`` 在调用时解析模块全局，``main`` 的 lifespan 与
    ``readyz`` 通过 ``app.main.engine`` 引用引擎，middleware / ipguard 直接持有
    会话工厂 —— 因此必须逐模块重绑，避免 import 时固化旧连接。

    注意这里引的是 ``app.kernel.middleware`` / ``app.kernel.ipguard`` 而不是
    ``app.core.*``：后者是 Phase 8 搬家留下的 re-export shim，给它赋属性只会改到
    shim 自己的命名空间，真正的中间件类读的还是 ``app.kernel.*`` 里的全局。
    """
    new_engine = create_async_engine(
        db_url,
        echo=settings.db_echo,
        future=True,
        pool_pre_ping=not settings.is_sqlite,
        **({} if settings.is_sqlite else {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow}),
    )
    new_factory = async_sessionmaker(new_engine, expire_on_commit=False, class_=AsyncSession)
    settings.db_url = db_url
    session_mod.engine = new_engine
    session_mod.async_session_factory = new_factory
    # 慢 SQL 钩子（Phase 11-2）只注册在导入时的全局引擎上；重建引擎后必须补挂，
    # 否则测试里的引擎与生产行为不一致（语句级慢日志永不触发）。
    from app.db.session import register_slow_sql_hooks

    register_slow_sql_hooks(new_engine.sync_engine)
    main_mod.engine = new_engine
    # startup / health 在 import 时各自固化了 `from app.db.session import engine`：
    # 漏重绑会让 lifespan 收尾 dispose 掉原始引擎（副本的连接/WAL 一直不释放，
    # Windows 上表现为临时目录删不掉），/readyz 也会查到错的库。
    startup_mod.engine = new_engine
    health_mod.engine = new_engine
    mw_mod.async_session_factory = new_factory
    ip_mod.async_session_factory = new_factory
    codegen_mod.engine = new_engine
    # codegen/router.py 在模块加载时固化了 generator = CodeGenerator(engine)，
    # 需要同步其持有的引擎引用，否则代码生成仍指向模板库
    codegen_mod.generator.engine = new_engine
    migrate_mod.engine = new_engine
    return new_engine


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """每个测试一份模板库副本 + 独立引擎，lifespan 退出时引擎由应用自行 dispose。"""
    global _current_test_db
    assert TEMPLATE_DB.exists(), "测试模板库缺失：_build_template 未成功"
    tmp_db = tmp_path / "test_fastcms.db"
    shutil.copyfile(TEMPLATE_DB, tmp_db)
    _repoint_engine(f"sqlite+aiosqlite:///{tmp_db.as_posix()}")
    _current_test_db = tmp_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        _current_test_db = None


@pytest.fixture
def admin_token(client: TestClient) -> str:
    data = _login(client, "admin", "Owner123456")
    return data["access_token"]


def login(client: TestClient, username: str, password: str) -> dict:
    return _login(client, username, password)


def auth(token: str) -> dict[str, str]:
    return _auth(token)
