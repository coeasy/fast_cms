from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.envelope import StrictModel
from app.modules.ops.audit.service import MAX_BATCH_DELETE_IDS


class IdBatchRequest(BaseModel):
    """按 id 批量删除日志。

    DELETE 带请求体必须显式声明 body 模型：裸 ``list[int]`` 会被 FastAPI 当成
    query 参数，JSON body 根本不会被读，于是前端明明传了 ids 却报
    「Input should be a valid list」。批量删用户走的是 ``POST`` 所以没踩到，
    这里是同一个坑的第二种触发方式。
    """

    ids: list[int] = Field(min_length=1, max_length=MAX_BATCH_DELETE_IDS)


class LoginLogOut(StrictModel):
    id: int
    username: str
    user_id: int | None = None
    status: str
    reason: str | None = None
    ip: str | None = None
    location: str | None = None
    user_agent: str | None = None
    created_at: str | None = None


class OperationLogOut(StrictModel):
    id: int
    user_id: int | None = None
    username: str | None = None
    module: str | None = None
    action: str | None = None
    method: str
    path: str
    status_code: int
    success: bool
    duration_ms: int
    ip: str | None = None
    location: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    created_at: str | None = None
    detail: str | None = None


class EventLogOut(StrictModel):
    id: int
    event: str
    subscriber: str
    success: bool
    duration_ms: int
    error: str | None = None
    payload_summary: str | None = None
    created_at: str | None = None
