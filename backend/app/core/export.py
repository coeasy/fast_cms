"""通用 CSV 导出。

审计日志最早各自实现了一份 CSV 拼装，用户、角色、字典、公告、任务日志要导出时
再抄第五遍没有意义，因此把「表头 + 行迭代 + 响应头 + 条数上限」收口到这里。

两个关键约定：

- **列白名单由调用方显式给出**：导出接口最容易把手机号、密码哈希之类的字段
  一起带出去，所以不允许直接序列化整个 ORM 对象；
- **有界物化**：这不是真流式——整份 CSV 会被一次性编码成一个分片返回。真正的
  约束是「边遍历边写 buffer + 行数硬上限」，所以内存上界由 ``limit`` 决定（10k 行
  量级只有几 MB），而不是由表有多大决定。选它不选真流式，是因为只有在返回前把行
  数数完，才能准确写出 ``X-Export-Rows`` / ``X-Export-Truncated`` 这两个头，前端
  依赖它们提示「已截断」。哪天要导出百万行，得改成生成器分片并放弃这两个头。
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

MAX_EXPORT_ROWS = 10000

# Excel / WPS / LibreOffice 会把以这些字符开头的单元格当**公式**执行。
# 公告标题、用户名、User-Agent 这些都是用户可控字段，一旦有人存了
# `=HYPERLINK("http://evil","点我")` 或 `=cmd|'/c calc'!A1`，导出后一打开就触发
# （公式注入 / 数据外泄 / 老版本 DDE 命令执行）。所以写 CSV 前统一中和。
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
# 负数与科学计数法不算公式：`-5`、`+3.2e8` 是数据，加前缀会破坏下游统计。
_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def neutralize_cell(value: Any) -> Any:
    """把「看起来像公式」的文本单元格转成纯文本。

    做法是加一个前导单引号：Excel 系产品会把它当「强制文本」标记并隐藏，
    而其它 CSV 消费方（pandas、数据库导入）看到的是明确的数据而非公式。
    代价是导出再导入会带上这个引号——相比「打开表格就执行远端公式」，这个代价是值得的。
    """
    if not isinstance(value, str) or not value:
        return value
    if not value.startswith(_FORMULA_LEAD):
        return value
    if _NUMBER_RE.match(value):
        return value
    return f"'{value}"


@dataclass(frozen=True)
class ExportColumn:
    """一列的定义；``key`` 只用于文档与自描述，真正取值由 ``value`` 决定。"""

    key: str
    header: str
    value: Any = None


def build_rows(
    items: Iterable[Any],
    columns: Sequence[ExportColumn],
) -> Iterable[list[Any]]:
    """把 ORM 对象按列定义转成二维数据；值为 ``None`` 统一成空串。"""
    for item in items:
        row: list[Any] = []
        for column in columns:
            value = column.value(item) if callable(column.value) else getattr(item, column.key, None)
            row.append(_cell(value))
        yield row


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return value


def csv_response(
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    filename: str,
    *,
    limit: int = MAX_EXPORT_ROWS,
) -> tuple[Any, int, bool]:
    """返回 ``(响应, 实际导出行数, 是否被截断)``。"""
    from fastapi.responses import StreamingResponse

    buffer = io.StringIO()
    # BOM：没有它 Excel 打开 UTF-8 CSV 会把中文显示成乱码
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow([neutralize_cell(item) for item in header])
    count = 0
    truncated = False
    for row in rows:
        if count >= limit:
            truncated = True
            break
        writer.writerow([neutralize_cell(item) for item in row])
        count += 1
    payload = buffer.getvalue().encode("utf-8")
    response = StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Export-Rows": str(count),
            "X-Export-Limit": str(limit),
            "X-Export-Truncated": "1" if truncated else "0",
        },
    )
    return response, count, truncated
