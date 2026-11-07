"""CSV 导出必须中和公式注入。

场景：管理员把公告标题存成 `=HYPERLINK("http://evil","点我")`，或用户名存成
`=cmd|'/c calc'!A1`；导出 CSV 后在 Excel / WPS 里打开，单元格以 `=` 开头就会被
当公式求值——轻则数据外泄（HYPERLINK 诱导点击、远程图片带参请求），重则老版本
Office 的 DDE 直接执行命令。

这里钉死三件事：

1. 危险前缀被转成纯文本（加前导单引号）；
2. 负数和科学计数法**不**被误伤（`-5` 是数据不是公式）；
3. 所有导出都经过 `csv_response` 这一个出口，因此都在保护范围内。
"""

from __future__ import annotations

import pytest

from app.core.export import MAX_EXPORT_ROWS, csv_response, neutralize_cell

DANGEROUS = [
    '=HYPERLINK("http://evil.example","点我")',
    "=cmd|'/c calc'!A1",
    "+1+1",
    "-1+1",  # 不是合法数字，按公式处理
    "@SUM(A1:A9)",
    "\t=1+1",
    "\r=1+1",
]


@pytest.mark.parametrize("payload", DANGEROUS)
def test_dangerous_prefix_is_neutralized(payload: str) -> None:
    result = neutralize_cell(payload)
    assert result == f"'{payload}", f"未中和：{payload!r} -> {result!r}"


@pytest.mark.parametrize("value", ["-5", "+3", "3.2e8", "-1.5E-3"])
def test_numbers_are_left_alone(value: str) -> None:
    assert neutralize_cell(value) == value


@pytest.mark.parametrize("value", ["普通标题", "", "a=b", "2026-10-28", None, 42, True])
def test_safe_values_are_untouched(value: object) -> None:
    assert neutralize_cell(value) == value


def _body(response) -> str:
    """StreamingResponse 的 body_iterator 是异步迭代器，测试里同步收一下。"""
    import asyncio

    async def collect() -> bytes:
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
        return b"".join(chunks)

    return asyncio.run(collect()).decode("utf-8")


def test_csv_response_neutralizes_every_row() -> None:
    """导出出口本身要挡住，而不是指望每个调用方自己转义。"""
    response, count, truncated = csv_response(
        ["标题", "操作用户"],
        [
            ['=HYPERLINK("http://evil.example")', "admin"],
            ["正常公告", "=1+1"],
            ["负数金额", "-12.5"],
        ],
        "test.csv",
    )
    assert count == 3
    assert not truncated

    body = _body(response)
    assert "'=HYPERLINK" in body
    assert "'=1+1" in body
    # 负数是数据，不能被加引号
    assert "-12.5" in body
    assert "'-12.5" not in body


def test_truncation_still_counts_only_written_rows() -> None:
    rows = [[f"row-{index}"] for index in range(5)]
    _, count, truncated = csv_response(["col"], rows, "test.csv", limit=3)
    assert count == 3
    assert truncated
    assert MAX_EXPORT_ROWS >= 3
