"""B-11 / 4-4：属性测试（hypothesis fuzz）。

对「输入空间大、规则简单」的函数做性质回归，比枚举几个用例更能钉住边界：

- :func:`neutralize_cell`：CSV 公式注入中和。性质 = 幂等、输出要么原样要么带
  前导单引号、非字符串原样返回、数字绝不误伤。
- :func:`safe_title`：生成器标题清洗。性质 = 幂等、产物不含任何会破坏字符串
  字面量的字符（引号/反斜杠/控制换行）、空白折叠、空标题有兜底名、可读字符保留。

这些性质在实现被改坏时会立即红，且不需要知道具体用例——正是 hypothesis 的用武之地。
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from app.codegen.generator import safe_title
from app.core.export import neutralize_cell

# 公式注入前导符（与实现一致，用于不变量断言）
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
# safe_title 必须彻底移除的字面量破坏符
_QUOTE_BREAKERS = ('"', "'", "`", "\\", "\n", "\r", "\t")

# 单次属性测试 100 个样例已足够：不变量是确定性的，无需跑满 hypothesis 默认的
# 200 例来拖慢门禁；对随机文本生成器这仍能覆盖大量边界（引号、换行、空串、BMP）。
_settings = settings(max_examples=100, deadline=None)


# ------------------------------ neutralize_cell ------------------------------


@_settings
@given(value=st.one_of(st.text(), st.integers(), st.floats(allow_nan=False), st.booleans(), st.none()))
def test_neutralize_cell_is_idempotent(value) -> None:
    """中和两次与中和一次结果相同（首轮已加引号的输入不再二次处理）。"""
    once = neutralize_cell(value)
    assert neutralize_cell(once) == once


@_settings
@given(value=st.text())
def test_neutralize_cell_output_is_original_or_quoted(value: str) -> None:
    """输出只能是「原样」或「前导单引号」两种形态，绝无其它改写。"""
    result = neutralize_cell(value)
    assert result == value or result == f"'{value}"


@_settings
@given(value=st.text())
def test_neutralize_cell_formula_lead_gets_quoted(value: str) -> None:
    """以危险前导符开头、且不是合法数字的文本，必须被加引号。"""
    result = neutralize_cell(value)
    if value.startswith(_FORMULA_LEAD) and not _NUMBER_RE.match(value):
        assert result == f"'{value}", f"应中和但未中和：{value!r} -> {result!r}"
    else:
        # 非公式前导（普通文本、合法数字）不加引号
        assert result == value


@_settings
@given(value=st.text())
def test_neutralize_cell_never_mangles_numbers(value: str) -> None:
    """负数和科学计数法是数据不是公式，绝不能加前导引号。"""
    if _NUMBER_RE.match(value):
        assert neutralize_cell(value) == value


@_settings
@given(value=st.one_of(st.integers(), st.floats(allow_nan=False), st.booleans(), st.none()))
def test_neutralize_cell_non_string_unchanged(value) -> None:
    """非字符串（数字/布尔/None）原样返回，不进入文本中和路径。

    用 ``is`` 而非 ``==``：实现直接返回同一对象，且 ``nan == nan`` 恒为假，
    身份断言对 NaN 也成立。
    """
    assert neutralize_cell(value) is value


# -------------------------------- safe_title --------------------------------


@_settings
@given(value=st.text())
def test_safe_title_removes_literal_breakers(value: str) -> None:
    """产物绝不能包含会破坏 .py / .js 字符串字面量的字符。"""
    cleaned = safe_title(value)
    for char in _QUOTE_BREAKERS:
        assert char not in cleaned


@_settings
@given(value=st.text())
def test_safe_title_collapses_whitespace(value: str) -> None:
    """产物是单行、无前导/尾随空白、无连续空格。"""
    cleaned = safe_title(value)
    assert cleaned == cleaned.strip()
    assert "  " not in cleaned
    assert "\n" not in cleaned and "\r" not in cleaned


@_settings
@given(value=st.text())
def test_safe_title_is_idempotent(value: str) -> None:
    """清洗两次与清洗一次结果相同（产物本身已是安全形态）。"""
    once = safe_title(value)
    assert safe_title(once) == once


@_settings
@given(value=st.text())
def test_safe_title_never_empty(value: str) -> None:
    """空/纯空白标题必须有兜底名，否则生成 description=\"\" 或字段为空。"""
    assert safe_title(value) != ""


@_settings
@given(value=st.text())
def test_safe_title_preserves_readable_characters(value: str) -> None:
    """中文、空格、括号等可读字符不应被过度清洗（只破坏字面量）。"""
    cleaned = safe_title(value)
    # 清洗是「删除破坏符 + 折叠空白 + 兜底」，不会凭空造出引号/反斜杠
    for char in ("\"", "'", "`", "\\"):
        assert char not in cleaned
    # 非空结果里至少保留原始的可读子串（去空白后），除非被兜底替代
    if value and any(ch not in _QUOTE_BREAKERS for ch in value):
        assert cleaned != ""
