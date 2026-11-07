"""P1-6：统一搜索入口（关键词长度上限 + LIKE 通配符转义）单元测试。"""

from __future__ import annotations

from app.core.search import (
    LIKE_ESCAPE,
    MAX_KEYWORD_LEN,
    contains,
    escape_like,
    normalize_keyword,
    prefix,
)


class TestNormalizeKeyword:
    def test_none_returns_none(self) -> None:
        assert normalize_keyword(None) is None

    def test_empty_and_whitespace_return_none(self) -> None:
        assert normalize_keyword("") is None
        assert normalize_keyword("   ") is None

    def test_strip_and_lower(self) -> None:
        assert normalize_keyword("  Hello WORLD  ") == "hello world"

    def test_truncates_overlong(self) -> None:
        long_input = "x" * (MAX_KEYWORD_LEN + 50)
        result = normalize_keyword(long_input)
        assert result is not None
        assert len(result) == MAX_KEYWORD_LEN
        assert result == "x" * MAX_KEYWORD_LEN

    def test_respects_custom_max_len(self) -> None:
        assert normalize_keyword("abcdef", max_len=3) == "abc"


class TestEscapeLike:
    def test_percent_is_escaped(self) -> None:
        assert escape_like("100%") == r"100\%"

    def test_underscore_is_escaped(self) -> None:
        assert escape_like("a_b") == r"a\_b"

    def test_backslash_is_escaped(self) -> None:
        assert escape_like(r"a\b") == r"a\\b"

    def test_plain_text_unchanged(self) -> None:
        assert escape_like("hello") == "hello"


class TestContains:
    def test_pattern_contains_and_escapes(self) -> None:
        # 子串匹配模式，通配符被转义为字面量
        assert contains("100%") == r"%100\%%"

    def test_none_when_no_keyword(self) -> None:
        assert contains("   ") is None
        assert contains(None) is None

    def test_truncates_long_keyword(self) -> None:
        result = contains("y" * (MAX_KEYWORD_LEN + 10))
        assert result is not None
        assert len(result) == MAX_KEYWORD_LEN + 2  # % + kw + %


class TestPrefix:
    def test_pattern_prefix(self) -> None:
        assert prefix("abc") == r"abc%"

    def test_escapes_wildcards(self) -> None:
        assert prefix("a_b") == r"a\_b%"

    def test_none_when_no_keyword(self) -> None:
        assert prefix("  ") is None
        assert prefix(None) is None


class TestEscapeConstant:
    def test_escape_char_is_single_backslash(self) -> None:
        assert LIKE_ESCAPE == "\\"
