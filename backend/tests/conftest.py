from __future__ import annotations

import os
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB = BACKEND_DIR / "data" / "test_fastcms.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEST_DB) + suffix).unlink(missing_ok=True)

os.environ["DB_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["INITIAL_ADMIN_PASSWORD"] = "RootInit12345"
os.environ["APP_ENV"] = "test"
os.environ["SCHEDULER_MODE"] = "disabled"
os.environ["APP_CORS_ORIGINS"] = "http://localhost:8000"
# 测试用通知渠道指向本地地址，放开内网校验才能覆盖完整发送链路
os.environ["NOTIFY_ALLOW_PRIVATE_TARGET"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def admin_token(client: TestClient) -> str:
    data = login(client, "admin", os.environ["INITIAL_ADMIN_PASSWORD"])
    response = client.post(
        "/api/v1/auth/change-password",
        headers=auth(data["access_token"]),
        json={"old_password": os.environ["INITIAL_ADMIN_PASSWORD"], "new_password": "Owner123456"},
    )
    assert response.status_code == 200, response.text
    return login(client, "admin", "Owner123456")["access_token"]
