"""错误文案单一来源（6-3）的回归测试。

三张表的约束靠 ``scripts/check_contract.py`` 在门禁里执行，这里把同一批规则
再钉一遍：门禁脚本本身被改坏时（例如有人顺手删掉一条校验），测试会立刻失败，
而不是等到某天错误响应里只剩下一个错误码。

覆盖两类：

1. **表自身的一致性**——错误码、默认文案、上下文例外三者一一对应；
2. **调用点的写法**——字面量文案、漏传 params、params 名字写错都要能被拦下。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_contract  # noqa: E402

from app.core.errors import ERROR_CODE_STATUS, AppError  # noqa: E402
from app.core.messages import CONTEXT_MESSAGES, MESSAGES, message_for  # noqa: E402


def _errors() -> list[str]:
    return []


def test_every_error_code_has_default_message() -> None:
    """新增错误码必须同时补默认文案，否则运行时 message 会退化成错误码本身。"""
    assert sorted(set(ERROR_CODE_STATUS) - set(MESSAGES)) == []


def test_no_orphan_message_keys() -> None:
    """表里不能留已删除错误码的文案——那是改完代码忘了清的死条目。"""
    assert sorted(set(MESSAGES) - set(ERROR_CODE_STATUS)) == []


def test_context_codes_are_registered_error_codes() -> None:
    assert sorted(CONTEXT_MESSAGES - set(ERROR_CODE_STATUS)) == []


def test_table_rules_pass_on_real_data() -> None:
    errors = _errors()
    check_contract.check_messages(errors)
    assert errors == []


def test_missing_entry_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(check_contract.MESSAGES, "SYSTEM_USER_NOT_FOUND")
    errors = _errors()
    check_contract.check_messages(errors)
    assert any("MESSAGES 缺少错误码 SYSTEM_USER_NOT_FOUND" in item for item in errors)


def test_orphan_entry_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(check_contract.MESSAGES, "SYSTEM_NOT_A_REAL_CODE", "占位")
    errors = _errors()
    check_contract.check_messages(errors)
    assert any("未定义错误码 SYSTEM_NOT_A_REAL_CODE" in item for item in errors)


def test_positional_placeholder_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """位置占位符一改顺序就静默错位，必须要求具名。"""
    monkeypatch.setitem(check_contract.MESSAGES, "SYSTEM_USER_NOT_FOUND", "用户 {} 不存在")
    errors = _errors()
    check_contract.check_messages(errors)
    assert any("位置占位符" in item for item in errors)


def test_empty_message_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(check_contract.MESSAGES, "SYSTEM_USER_NOT_FOUND", "   ")
    errors = _errors()
    check_contract.check_messages(errors)
    assert any("文案为空" in item for item in errors)


def _call(source: str) -> ast.Call:
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.Expr)
    call = node.value
    assert isinstance(call, ast.Call)
    return call


def _usage_errors(call: ast.Call) -> list[str]:
    params = next((kw.value for kw in call.keywords if kw.arg == "params"), None)
    errors = _errors()
    check_contract._check_message_usage(
        errors, "probe:1", call.args[0].value, call.args[1] if len(call.args) > 1 else None, params
    )
    return errors


def test_literal_message_on_plain_code_is_rejected() -> None:
    errors = _usage_errors(_call('AppError("SYSTEM_USER_NOT_FOUND", "用户不见了", 404)'))
    assert errors and "写死了文案" in errors[0]


def test_literal_message_on_context_code_is_allowed() -> None:
    code = next(iter(sorted(CONTEXT_MESSAGES)))
    errors = _usage_errors(_call(f'AppError("{code}", "自定义措辞", 400)'))
    assert errors == []


def test_missing_params_for_template_is_rejected() -> None:
    errors = _usage_errors(_call('AppError("IMPORT_TOO_MANY_ROWS", status_code=422)'))
    assert errors and "必须传 params" in errors[0]


def test_params_name_mismatch_is_rejected() -> None:
    errors = _usage_errors(_call('AppError("IMPORT_TOO_LARGE", status_code=413, params={"size": 1})'))
    assert any("缺少占位符实参" in item for item in errors)
    assert any("多出模板里没有的实参" in item for item in errors)


def test_params_without_placeholder_is_rejected() -> None:
    errors = _usage_errors(_call('AppError("SYSTEM_ROLE_EXISTS", status_code=409, params={"x": 1})'))
    assert any("没有占位符" in item for item in errors)


def test_message_and_params_together_is_rejected() -> None:
    errors = _usage_errors(
        _call('AppError("IMPORT_TOO_MANY_ROWS", "写死", status_code=422, params={"max_rows": 1})')
    )
    assert any("同时传了 message 与 params" in item for item in errors)


def test_message_for_falls_back_to_code() -> None:
    """异常构造路径上不能再抛异常，未知错误码原样返回。"""
    assert message_for("NOT_REGISTERED") == "NOT_REGISTERED"


def test_message_for_fills_params() -> None:
    assert message_for("IMPORT_TOO_MANY_ROWS", {"max_rows": 500}) == MESSAGES["IMPORT_TOO_MANY_ROWS"].format(
        max_rows=500
    )


def test_message_for_keeps_template_when_params_missing() -> None:
    assert message_for("IMPORT_TOO_MANY_ROWS") == MESSAGES["IMPORT_TOO_MANY_ROWS"]


def test_message_for_keeps_template_when_params_broken() -> None:
    assert message_for("IMPORT_TOO_MANY_ROWS", {"wrong": 1}) == MESSAGES["IMPORT_TOO_MANY_ROWS"]


def test_app_error_defaults_to_table_message() -> None:
    error = AppError("SYSTEM_USER_NOT_FOUND")
    assert error.message == MESSAGES["SYSTEM_USER_NOT_FOUND"]
    assert error.status_code == ERROR_CODE_STATUS["SYSTEM_USER_NOT_FOUND"]


def test_app_error_explicit_message_wins() -> None:
    error = AppError("SYSTEM_USER_NOT_FOUND", "指定的用户不存在")
    assert error.message == "指定的用户不存在"


def test_app_error_renders_params() -> None:
    error = AppError("IMPORT_TOO_MANY_ROWS", params={"max_rows": 12})
    assert "12" in error.message
    assert "{" not in error.message


def test_app_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="未注册的业务错误码"):
        AppError("NOT_REGISTERED")


def test_app_error_rejects_status_drift() -> None:
    with pytest.raises(ValueError, match="状态码漂移"):
        AppError("SYSTEM_USER_NOT_FOUND", status_code=422)
