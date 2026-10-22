"""通用 CSV 导出。

审计日志最早各自实现了一份 CSV 拼装，用户、角色、字典、公告、任务日志要导出时
再抄第五遍没有意义，因此把「表头 + 行迭代 + 响应头 + 条数上限」收口到这里。

两个关键约定：

- **列白名单由调用方显式给出**：导出接口最容易把手机号、密码哈希之类的字段
  一起带出去，所以不允许直接序列化整个 ORM 对象；
- **流式响应**：先算好行再一次性返回会在大数据集上把内存打满，
  这里边遍历边写 buffer，并对行数设上限，超出部分截断并在响应头里说明。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

MAX_EXPORT_ROWS = 10000


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
    writer.writerow(list(header))
    count = 0
    truncated = False
    for row in rows:
        if count >= limit:
            truncated = True
            break
        writer.writerow(list(row))
        count += 1
    payload = buffer.getvalue().encode("utf-8")
    response = StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Export-Rows": str(count),
            "X-Export-Truncated": "1" if truncated else "0",
        },
    )
    return response, count, truncated
