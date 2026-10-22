from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

HTTP_ERROR_MESSAGES: dict[int, str] = {
    401: "请先登录",
    403: "没有权限执行该操作",
    404: "请求的资源不存在",
    405: "请求方法不被允许",
    413: "请求内容过大",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后再试",
}


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Any = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


def get_request_id() -> str:
    return request_id_var.get()


def success(data: Any = None, message: str = "success") -> dict[str, Any]:
    return {
        "code": "OK",
        "message": message,
        "data": data,
        "request_id": get_request_id(),
    }


def error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": get_request_id(),
    }


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, exc.details),
    )


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    from fastapi.exceptions import RequestValidationError

    assert isinstance(exc, RequestValidationError)
    details = []
    for item in exc.errors():
        details.append(
            {
                "field": ".".join(str(part) for part in item.get("loc", [])[1:]),
                "message": item.get("msg", "invalid value"),
                "type": item.get("type", "validation_error"),
            }
        )
    return JSONResponse(
        status_code=422,
        content=error_payload("VALIDATION_ERROR", "请求参数校验失败", details),
    )


async def http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else None
    message = HTTP_ERROR_MESSAGES.get(exc.status_code) or detail or "请求处理失败"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            f"HTTP_{exc.status_code}",
            message,
            None if detail else exc.detail,
        ),
    )


async def unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", "服务器内部错误，请使用 request_id 联系管理员"),
    )
