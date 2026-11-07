from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.messages import message_for

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# 框架层 HTTP 异常的中文文案。业务错误码的文案在 core/messages.py 的 MESSAGES 表，
# 这里只覆盖「没有业务错误码」的框架兜底（路由不存在、方法不允许等），
# 两者边界：ERROR_CODE_STATUS 里有的走 MESSAGES，其余走本表。
HTTP_ERROR_MESSAGES: dict[int, str] = {
    401: "请先登录",
    403: "没有权限执行该操作",
    404: "请求的资源不存在",
    405: "请求方法不被允许",
    413: "请求内容过大",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后再试",
}

# ---------------------------------------------------------------------------
# 业务错误码 -> HTTP 状态码的唯一映射表（P2-4）。
#
# 单一事实源：每个业务错误码有且只有一个 HTTP 状态码。调用方通过
# ``AppError(code, message)`` 触发时不再自行决定状态码，而是由本表解析；
# 若显式传入 ``status_code``，构造器会校验其与本表一致，不一致即抛
# ``ValueError``，在开发/测试期就暴露「错误码与状态码漂移」。
#
# 新增错误码必须同时登记到本表，否则 ``AppError`` 构造时立即报错。
# 本表与 ``scripts/check_contract.py`` 的 AST 扫描共同构成契约校验。
# ---------------------------------------------------------------------------
ERROR_CODE_STATUS: dict[str, int] = {
    "AUDIT_BATCH_EMPTY": 422,
    "AUDIT_BATCH_TOO_MANY": 422,
    "AUDIT_LOG_NOT_FOUND": 404,
    "AUDIT_RETENTION_TOO_SHORT": 422,
    "AUTH_ACCOUNT_BANNED": 403,
    "AUTH_ACCOUNT_LOCKED": 423,
    "AUTH_AVATAR_NOT_FOUND": 404,
    "AUTH_FORBIDDEN": 403,
    "AUTH_INVALID_CREDENTIALS": 401,
    "AUTH_OLD_PASSWORD_INVALID": 400,
    "AUTH_ORIGIN_DENIED": 403,
    "AUTH_PASSWORD_CHANGE_REQUIRED": 403,
    "AUTH_PASSWORD_UNCHANGED": 422,
    "AUTH_PERMISSION_SUNSET": 403,
    "AUTH_RATE_LIMITED": 429,
    "AUTH_REFRESH_INVALID": 401,
    "AUTH_REFRESH_REUSED": 401,
    "AUTH_SERVICE_ACCOUNT_LOGIN": 403,
    "AUTH_SESSION_EXPIRED": 401,
    "AUTH_SESSION_NOT_FOUND": 404,
    "AUTH_SESSION_REQUIRED": 400,
    "AUTH_TOKEN_INVALID": 401,
    "AUTH_UNAUTHORIZED": 401,
    "AUTH_USER_DISABLED": 401,
    "AUTH_WEAK_PASSWORD": 422,
    "CODEGEN_COMPOSITE_KEY": 422,
    "CODEGEN_FILE_EXISTS": 409,
    "CODEGEN_MODULE_INVALID": 422,
    "CODEGEN_NO_PRIMARY_KEY": 422,
    "CODEGEN_PATH_INVALID": 422,
    "CODEGEN_RESOURCE_INVALID": 422,
    "CODEGEN_TABLE_INVALID": 422,
    "CODEGEN_TABLE_NOT_FOUND": 404,
    "CODEGEN_TABLE_SYSTEM": 422,
    "CODEGEN_TARGET_EXISTS": 409,
    "CONFIG_BUILTIN": 409,
    "CONFIG_DICT_EXISTS": 409,
    "CONFIG_DICT_ITEM_EXISTS": 409,
    "CONFIG_DICT_ITEM_NOT_FOUND": 404,
    "CONFIG_DICT_NOT_FOUND": 404,
    "CONFIG_KEY_EXISTS": 409,
    "CONFIG_NOT_FOUND": 404,
    "CONFIG_TIMEZONE_INVALID": 422,
    "CONFIG_VALUE_INVALID": 422,
    "CONTENT_NOTICE_NOT_FOUND": 404,
    "CONTENT_NOTICE_NOT_PUBLISHED": 400,
    "DATA_CONFLICT": 409,
    "EXPORT_SPEC_UNKNOWN": 404,
    "IMPORT_COLUMN_MISSING": 422,
    "IMPORT_COLUMN_UNKNOWN": 422,
    "IMPORT_EMPTY": 422,
    "IMPORT_ENCODING": 422,
    "IMPORT_ROW_INVALID": 422,
    "IMPORT_TOO_LARGE": 413,
    "IMPORT_TOO_MANY_ROWS": 422,
    "MONITOR_KICK_FORBIDDEN": 403,
    "NOTIFY_CHANNEL_EXISTS": 409,
    "NOTIFY_CHANNEL_NOT_FOUND": 404,
    "NOTIFY_RECORD_CONFLICT": 409,
    "NOTIFY_RECORD_NOT_FOUND": 404,
    "NOTIFY_RECORD_SENDING": 409,
    "NOTIFY_RECORD_SENT": 409,
    "NOTIFY_URL_INVALID": 422,
    "SCHEDULE_CRON_INVALID": 422,
    "SCHEDULE_CRON_REQUIRED": 422,
    "SCHEDULE_DATE_PAST": 422,
    "SCHEDULE_DATE_REQUIRED": 422,
    "SCHEDULE_DATE_TIMEZONE_REQUIRED": 422,
    "SCHEDULE_INTERVAL_INVALID": 422,
    "SCHEDULE_JOB_ARGS_INVALID": 422,
    "SCHEDULE_JOB_BUILTIN": 409,
    "SCHEDULE_JOB_CODE_EXISTS": 409,
    "SCHEDULE_JOB_CODE_FORBIDDEN": 403,
    "SCHEDULE_JOB_NOT_FOUND": 404,
    "SCHEDULE_JOB_TYPE_INVALID": 422,
    "SCHEDULE_JOB_UNKNOWN": 422,
    "SCHEDULE_LOG_NOT_FOUND": 404,
    "SCHEDULE_PYTHON_DISABLED": 403,
    "SCHEDULE_SCRIPT_INVALID": 422,
    "SCHEDULE_SCRIPT_REQUIRED": 422,
    "SCHEDULE_SCRIPT_TOO_LARGE": 422,
    "SCHEDULE_STATE_INVALID": 422,
    "SCHEDULE_TEMPLATE_BUILTIN": 409,
    "SCHEDULE_TEMPLATE_CODE_EXISTS": 409,
    "SCHEDULE_TEMPLATE_HANDLER_MISMATCH": 422,
    "SCHEDULE_TEMPLATE_HANDLER_REQUIRED": 422,
    "SCHEDULE_TEMPLATE_NOT_FOUND": 404,
    "SCHEDULE_TEMPLATE_TYPE_MISMATCH": 422,
    "SCHEDULE_TIMEZONE_INVALID": 422,
    "SCHEDULE_TRIGGER_INVALID": 422,
    "SECURITY_API_KEY_DUPLICATED": 409,
    "SECURITY_API_KEY_NOT_FOUND": 404,
    "SECURITY_API_KEY_SCOPE_FORBIDDEN": 403,
    "SECURITY_API_KEY_SCOPE_UNKNOWN": 422,
    "SECURITY_API_KEY_STATUS_INVALID": 422,
    "SECURITY_BAN_SUPERUSER": 403,
    "SECURITY_IP_INVALID": 422,
    "SECURITY_IP_RULE_DUPLICATED": 409,
    "SECURITY_IP_RULE_NOT_FOUND": 404,
    "SECURITY_IP_RULE_STATUS_INVALID": 422,
    "SECURITY_IP_UNKNOWN": 422,
    "SECURITY_SELF_BAN": 409,
    "SYSTEM_BATCH_ACTION_INVALID": 422,
    "SYSTEM_BATCH_EMPTY": 422,
    "SYSTEM_BATCH_TOO_MANY": 422,
    "SYSTEM_BUILTIN_USER": 409,
    "SYSTEM_DEPT_CYCLE": 422,
    "SYSTEM_DEPT_FORBIDDEN": 403,
    "SYSTEM_DEPT_HAS_CHILDREN": 409,
    "SYSTEM_DEPT_IN_USE": 409,
    "SYSTEM_DEPT_NOT_FOUND": 404,
    "SYSTEM_DEPT_TOO_DEEP": 422,
    "SYSTEM_LAST_SUPERUSER": 409,
    "SYSTEM_MENU_BUILTIN": 409,
    "SYSTEM_MENU_CYCLE": 422,
    "SYSTEM_MENU_HAS_CHILDREN": 409,
    "SYSTEM_MENU_NOT_FOUND": 404,
    "SYSTEM_MENU_PARENT_INVALID": 422,
    "SYSTEM_MENU_PARENT_NOT_FOUND": 404,
    "SYSTEM_MENU_PATH_EXISTS": 409,
    "SYSTEM_MENU_PATH_REQUIRED": 422,
    "SYSTEM_MENU_PERMISSION_REQUIRED": 422,
    "SYSTEM_PERMISSION_NOT_FOUND": 404,
    "SYSTEM_PROFILE_AVATAR_INVALID": 422,
    "SYSTEM_ROLE_BUILTIN": 409,
    "SYSTEM_ROLE_DISABLED": 409,
    "SYSTEM_ROLE_ESCALATION": 403,
    "SYSTEM_ROLE_EXISTS": 409,
    "SYSTEM_ROLE_IN_USE": 409,
    "SYSTEM_ROLE_NOT_FOUND": 404,
    "SYSTEM_SELF_DELETE": 409,
    "SYSTEM_SELF_DEMOTE": 409,
    "SYSTEM_SELF_DISABLE": 409,
    "SYSTEM_USERNAME_EXISTS": 409,
    "SYSTEM_USER_DEPT_FORBIDDEN": 403,
    "SYSTEM_USER_FORBIDDEN": 403,
    "SYSTEM_USER_NOT_FOUND": 404,
    "SYSTEM_WEAK_PASSWORD": 422,
    "THEME_ACTIVE": 409,
    "THEME_BUILTIN": 409,
    "THEME_CODE_EXISTS": 409,
    "THEME_NOT_FOUND": 404,
    "TOOL_FILE_EMPTY": 422,
    "TOOL_FILE_FORBIDDEN": 403,
    "TOOL_FILE_MISSING": 404,
    "TOOL_FILE_NOT_FOUND": 404,
    "TOOL_FILE_NO_EXTENSION": 422,
    "TOOL_FILE_PATH_INVALID": 400,
    "TOOL_FILE_TOO_LARGE": 413,
    "TOOL_FILE_TYPE_FORBIDDEN": 422,
    "TOOL_FILE_TYPE_MISMATCH": 422,
}


class AppError(Exception):
    """业务错误。

    ``message`` 可以省略——省略时从 ``core.messages.MESSAGES`` 取该错误码的默认
    文案（B-10 / 6-3）。推荐写法是只传错误码：

        raise AppError("SYSTEM_USER_NOT_FOUND")

    只有 ``core.messages.CONTEXT_MESSAGES`` 里登记的错误码（同一码在不同场景需要
    不同措辞）才在调用点显式传 ``message``；其余码传字面量文案会被契约门禁拦下。
    需要把变量塞进文案时用 ``params``，模板与实参分离，文案改动只需改表：

        raise AppError("IMPORT_TOO_MANY_ROWS", params={"max_rows": max_rows})
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        try:
            expected = ERROR_CODE_STATUS[code]
        except KeyError:
            raise ValueError(f"未注册的业务错误码：{code!r}（请登记到 core.errors.ERROR_CODE_STATUS）") from None
        if status_code is None:
            status_code = expected
        elif status_code != expected:
            raise ValueError(f"错误码 {code} 的状态码漂移：调用处 {status_code} != 契约 {expected}")
        self.code = code
        self.message = message if message is not None else message_for(code, params)
        self.status_code = status_code
        self.details = details
        super().__init__(self.message)


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


async def integrity_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """兜底：未显式处理的唯一约束 / 外键冲突统一回 409，而不是 500。

    各模块的「先查后插」预检能挡住绝大多数重复，但两个请求同时通过预检时，
    真正兜底的是数据库约束——那一刻抛的是 ``IntegrityError``。若没人接，
    客户端拿到的是 500「服务器内部错误」，而实际是「数据重复」，既误导用户
    也污染错误率指标。

    ``kernel.db.commit()`` 已经做了同样的翻译，但存量里仍有大量直接
    ``await db.commit()`` 的调用点；这一层保证漏网的那部分也有正确的语义。

    异常原文含 SQL 与参数，只进日志不返回给调用方。
    """
    from sqlalchemy.exc import IntegrityError

    if not isinstance(exc, IntegrityError):
        return await unhandled_error_handler(_request, exc)
    import logging

    logging.getLogger("fastcms").warning(
        "数据库约束冲突 request_id=%s error=%s", get_request_id(), exc.__class__.__name__
    )
    return JSONResponse(
        status_code=409,
        content=error_payload("DATA_CONFLICT", "数据重复或违反约束，请检查后重试"),
    )
