"""统一关键词搜索工具。

P1-6：多个 service 各自内联 ``like = f"%{keyword.strip().lower()}%"``，导致：

1. 关键词长度无上限，超长输入会把一整段 SQL LIKE 模式塞进查询，DB 需先物化模式再扫描；
2. ``%``/``_`` 未转义，用户输入 ``100%`` 会被当作通配符，搜索语义与直觉不符；
3. 前置通配 ``%kw%`` 无法走索引，大表退化为全表扫描。

本模块是唯一入口：

- :func:`normalize_keyword` 统一清洗（strip、lower、截断到 ``max_len``、空串归一为 ``None``）；
- :func:`contains` / :func:`prefix` 生成带转义的模式，配合 ``escape="\\\\"`` 使用；
- :func:`LIKE_ESCAPE` 作为 ``like(pattern, escape=...)`` 的固定转义符。

各资源可按规模选择 ``contains``（小表、需要子串匹配）或 ``prefix``（可走前缀索引）。
关键词长度统一收敛在 ``MAX_KEYWORD_LEN``，避免无界模式。
"""

from __future__ import annotations

import re

#: 关键词最大长度。超过部分直接截断，避免超长模式撑大 LIKE 与扫描成本。
MAX_KEYWORD_LEN = 64

#: 供 ``column.like(pattern, escape=LIKE_ESCAPE)`` 使用的转义符。
LIKE_ESCAPE = "\\"

#: 匹配 ``%``、``_``、``\`` 三个 LIKE 元字符，用于转义成字面量。
_SPECIAL_RE = re.compile(r"([%_\\])")


def escape_like(raw: str) -> str:
    """把 LIKE 元字符（``%``/``_``/``\\``）转义为字面量。

    这样用户搜 ``100%`` 只会命中真正含 ``100%`` 的记录，而不会被当作通配符。
    """
    return _SPECIAL_RE.sub(r"\\\1", raw)


def normalize_keyword(raw: str | None, *, max_len: int = MAX_KEYWORD_LEN) -> str | None:
    """清洗关键词：strip + 统一小写 + 截断到 ``max_len``。

    返回 ``None`` 表示没有有效关键词（调用方应跳过过滤）。
    """
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if len(value) > max_len:
        value = value[:max_len]
    return value


def contains(raw: str | None, *, max_len: int = MAX_KEYWORD_LEN) -> str | None:
    """子串匹配模式 ``%kw%``；无有效关键词返回 ``None``。"""
    kw = normalize_keyword(raw, max_len=max_len)
    if kw is None:
        return None
    return f"%{escape_like(kw)}%"


def prefix(raw: str | None, *, max_len: int = MAX_KEYWORD_LEN) -> str | None:
    """前缀匹配模式 ``kw%``；无有效关键词返回 ``None``。可配合前缀索引。

    目前业务列表统一用 ``contains``；本函数与它共用清洗/转义口径，
    供未来大表切换前缀匹配时直接使用，避免再手写一份模式拼接。
    """
    kw = normalize_keyword(raw, max_len=max_len)
    if kw is None:
        return None
    return f"{escape_like(kw)}%"
