"""错误码 → HTTP 状态码唯一映射契约测试（P2-4）。

验收点：
- 每个登记的错误码解析到且仅到一个 HTTP 状态码；
- ``AppError`` 未传状态码时从映射解析，传错状态码立即抛 ValueError（防漂移）；
- 未登记的错误码在构造时即失败，不能静默通过；
- 状态码取值都在合法 HTTP 语义范围内（4xx 客户端错误）。
"""

from __future__ import annotations

import pytest

from app.core.errors import ERROR_CODE_STATUS, AppError


def test_mapping_nonempty_and_statuses_valid() -> None:
    assert len(ERROR_CODE_STATUS) >= 100, "错误码表不应为空"
    for code, status in ERROR_CODE_STATUS.items():
        assert isinstance(code, str) and code
        assert isinstance(status, int)
        assert 400 <= status <= 499, f"{code} 的状态码 {status} 不是 4xx 客户端错误"


def test_app_error_derives_status_from_mapping() -> None:
    code, expected = next(iter(ERROR_CODE_STATUS.items()))
    error = AppError(code, "测试消息")
    assert error.code == code
    assert error.status_code == expected
    assert error.message == "测试消息"


def test_app_error_accepts_matching_status() -> None:
    code, expected = next(iter(ERROR_CODE_STATUS.items()))
    error = AppError(code, "测试消息", expected)
    assert error.status_code == expected


def test_app_error_rejects_mismatched_status() -> None:
    code, expected = next(iter(ERROR_CODE_STATUS.items()))
    mismatch = 422 if expected != 422 else 409
    with pytest.raises(ValueError, match="状态码漂移"):
        AppError(code, "测试消息", mismatch)


def test_app_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="未注册"):
        AppError("NO_SUCH_ERROR_CODE", "测试消息")


def test_app_error_rejects_unknown_code_even_with_status() -> None:
    with pytest.raises(ValueError, match="未注册"):
        AppError("NO_SUCH_ERROR_CODE", "测试消息", 400)
