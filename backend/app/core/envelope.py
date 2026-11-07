"""统一响应信封：所有接口的 ``response_model`` 基座。

运行时 :func:`app.core.errors.success` 返回的是普通 dict；本模块定义的是
**响应契约**——既写进 OpenAPI（前端可据此生成类型/写文档），也让 FastAPI 在
出站时校验一遍，防止响应结构悄悄漂移。

历史问题：153 个操作的 200 响应 schema 全是空对象 ``{}``，等于没有任何响应
契约。这类缺口不会被静态检查发现，却真实咬过人——``/security/ip-locate``
曾把含 ``bytes`` 的内部对象塞进 ``data``，一装好库就 500。有了 ``Envelope``
之后，同样的错误会在序列化前变成明确的校验失败。

用法::

    @router.get("/content/notices", response_model=Envelope[Page[NoticeOut]])
    async def list_notices(...) -> Envelope[Page[NoticeOut]]:
        return success(page_data(items, total, params))
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """响应模型基类：**多出字段即校验失败**。

    服务层返回的是普通 dict（``notice_out()`` 这类），响应模型是另一份字段清单。
    Pydantic 默认 ``extra="ignore"``，于是「服务层加了字段、模型忘了加」时，
    新字段会被**静默丢弃**——HTTP 200、无日志，前端只会拿到 undefined。

    开启 ``forbid`` 后，这类漂移在测试里立刻变成 ``ResponseValidationError``，
    而不是悄悄少一个字段。``Envelope``/``Page`` 这些信封结构保持宽松：
    它们由框架层构造，不需要这层保护。
    """

    model_config = ConfigDict(extra="forbid")


class Envelope[T](BaseModel):
    """``{"code": "OK", "message": ..., "data": ..., "request_id": ...}``。"""

    code: Literal["OK"] = "OK"
    message: str = "success"
    data: T | None = None
    request_id: str = ""


class Page[T](BaseModel):
    """``page_data()`` 的形状；``total`` 在 ``total=false`` 时缺省。"""

    items: list[T] = Field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total: int | None = None


class KeysetPage[T](Page[T]):
    """offset 分页 + 可选游标：深分页时 ``keyset=true`` 会多带一个 ``next_cursor``。"""

    next_cursor: Any | None = None


class CountResult(BaseModel):
    """``{"count": n}`` / ``{"deleted": n}`` 这类只回计数的写操作。"""

    count: int = 0


class DeletedResult(BaseModel):
    deleted: int = 0


class FailedItem(BaseModel):
    """批量操作中单条失败的原因（成功的不逐个回传错误码）。"""

    id: int
    code: str
    message: str


class BatchOutcome(BaseModel):
    """批量操作结果：成功与失败分开回传，前端按结果提示而不是整体回滚。"""

    succeeded: list[int] = Field(default_factory=list)
    failed: list[FailedItem] = Field(default_factory=list)


# 文件流端点没有 JSON 信封，但同样要有响应契约：否则 OpenAPI 里是空 schema，
# 前端生成不出下载类型，契约门禁也无从判断「这个接口到底回什么」。
CSV_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "CSV 文件流（响应头 X-Export-Rows / X-Export-Limit / X-Export-Truncated 说明是否导全）",
        "content": {"text/csv": {"schema": {"type": "string"}}},
    }
}

BINARY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "二进制文件流",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}
