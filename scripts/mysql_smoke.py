"""冒烟验证：确认应用真的连上了预期数据库，并且核心接口可用。

用在迁移到 MySQL / Redis 之后：迁移脚本本身跑通不等于应用真在用新库，
常见问题是环境变量没生效，迁移打到 MySQL 而应用仍在写 SQLite。

检查项：
1. 配置的 DB_URL 方言与预期一致（``--expect mysql`` / ``sqlite``）
2. 实际建立连接并执行查询，确认不是「配置对但连不上」
3. ``/healthz`` 存活
4. ``/readyz`` 就绪（内部比对 alembic 版本与代码 head）
5. 用 ``INITIAL_ADMIN_PASSWORD`` 登录成功
6. ``/meta/version`` 匿名可访问

用法（与 docs/部署与升级.md 一致）：

    cd backend
    DB_URL='mysql+aiomysql://fastcms:fastcms@127.0.0.1:3306/fastcms' \
    INITIAL_ADMIN_PASSWORD='一次性的初始密码' \
        .venv/bin/python ../scripts/mysql_smoke.py

不传 ``--expect`` 时以 DB_URL 里的方言为准，因此对 SQLite 与 MySQL 都适用。
任一项失败即返回非 0，可直接放进部署脚本或 CI。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# TestClient 通过 app.main 导入，而包在 backend/ 下
sys.path.insert(0, str(BACKEND))


def read_expected_admin_password() -> str:
    """取 INITIAL_ADMIN_PASSWORD，开发默认口令作为兜底。"""
    return os.environ.get("INITIAL_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or "fast_cms_123"


def check_dialect() -> tuple[str, str]:
    """返回 (方言名, DB_URL)。方言取自配置，不依赖任何接口。"""
    from sqlalchemy.engine import make_url

    from app.core.config import settings

    url = make_url(settings.db_url)
    return url.get_backend_name(), settings.db_url


def check_connectivity() -> str:
    """真正建立一次连接，排除「配置正确但网络不通」的假阳性。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.core.config import settings

    # 同步驱动才能用 TestClient 同进程验证；aiosqlite/aiomysql 换成 sqlite/mysql
    raw = settings.db_url
    mapped = raw.replace("+aiosqlite", "").replace("+aiomysql", "")
    mapped = mapped.replace("://", "+pymysql://", 1) if mapped.startswith("mysql") else mapped
    dialect = make_url(raw).get_backend_name()
    engine = create_engine(mapped)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    return dialect


def check_http(api_prefix: str, admin_password: str) -> None:
    """顺序跑通健康检查、登录与匿名版本接口。"""
    from fastapi.testclient import TestClient

    from app.main import app

    token: str | None = None
    with TestClient(app) as client:
        for path in ("/healthz", "/readyz"):
            response = client.get(path)
            assert response.status_code == 200, f"{path} 返回 {response.status_code}：{response.text[:200]}"
            print(f"  OK  {path}")

        version = client.get(f"{api_prefix}/meta/version")
        assert version.status_code == 200, f"meta/version 返回 {version.status_code}：{version.text[:200]}"
        print(f"  OK  {api_prefix}/meta/version（匿名可访问）")

        login = client.post(f"{api_prefix}/auth/login", json={"username": "admin", "password": admin_password})
        assert login.status_code == 200, (
            f"登录失败：HTTP {login.status_code}；检查 INITIAL_ADMIN_PASSWORD 是否与该库的首次初始化口令一致"
        )
        token = login.json().get("data", {}).get("accessToken") or login.json().get("data", {}).get("access_token")
        assert token, f"登录响应中取不到令牌：{login.text[:200]}"
        print("  OK  使用 INITIAL_ADMIN_PASSWORD 登录")

        protected = client.get(f"{api_prefix}/monitor/server", headers={"Authorization": f"Bearer {token}"})
        assert protected.status_code == 200, f"受保护接口 /monitor/server 返回 {protected.status_code}：{protected.text[:200]}"
        print("  OK  /monitor/server（需要登录态）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="迁移后冒烟验证：数据库方言、连通性与核心接口")
    parser.add_argument("--expect", choices=("sqlite", "mysql"), help="期望的数据库方言，默认取 DB_URL")
    parser.add_argument("--admin-password", default=None, help="覆盖 INITIAL_ADMIN_PASSWORD 环境变量")
    args = parser.parse_args(argv)

    failures: list[str] = []
    try:
        from app.core.config import settings

        dialect, db_url = check_dialect()
        expected = args.expect or dialect
        safe_url = db_url.replace("@", "@***@") if "@" in db_url else db_url
        print(f"配置的 DB_URL：{safe_url}")
        print(f"方言：{dialect}（期望 {expected}）")
        if dialect != expected:
            failures.append(f"方言不一致：配置为 {dialect}，期望 {expected}")

        actual = check_connectivity()
        print(f"  OK  实际连接成功（{actual}）")
        if actual != expected:
            failures.append(f"实际连接的方言 {actual} 与期望 {expected} 不一致")

        print("HTTP 检查：")
        check_http(settings.api_prefix, args.admin_password or read_expected_admin_password())
    except AssertionError as exc:
        failures.append(str(exc))
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")

    if failures:
        print()
        print("冒烟验证未通过：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("冒烟验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
