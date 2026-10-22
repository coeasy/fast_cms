"""CSV 导入读取。

管理后台的导入入口统一走这里，集中处理三件最容易出错、且错了很难排查的事：

- **BOM**：Excel「另存为 UTF-8 CSV」会带 BOM，不剥掉的话第一个列名变成
  ``\\ufeffusername``，于是「用户名」列永远读不到，报错还指向错误的列；
- **表头归一**：中英文列名都能认。运营按模板填了中文表头，不该被一句
  「缺少 username」打发回去；
- **空行与注释行**： trailing 空行、以 ``#`` 开头的说明行都要跳过，
  否则最后一行会变成一条全是空字段的脏数据。

编码按 UTF-8 → GBK 顺序尝试：国内用户的表格绝大多数是这两种，
  但 GBK 会把合法 UTF-8 中文误判成功，所以必须让 UTF-8 先试。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence

from app.core.errors import AppError

# 单次导入的行数上限。再大的量应该分文件导，否则一条记录失败要重跑整包。
MAX_IMPORT_ROWS = 500


def decode_csv(raw: bytes) -> str:
    """按 UTF-8(带/不带 BOM) → GBK 顺序解码；都失败就明确告诉用户另存为 UTF-8。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AppError("IMPORT_ENCODING", "无法识别文件编码，请另存为 UTF-8 或 GBK 编码的 CSV", 422)


def read_rows(
    raw: bytes,
    *,
    columns: Mapping[str, str],
    required: Sequence[str] = (),
    max_rows: int = MAX_IMPORT_ROWS,
) -> list[dict[str, str]]:
    """把 CSV 读成规范字段名的字典列表。

    ``columns`` 是「可接受的表头（小写）→ 规范字段名」的映射，必须包含规范
    字段名本身，否则英文表头反而读不进来。表头出现映射外的列直接报错并列出
    来——列名打错时静默丢掉该列，比报错危险得多。
    """
    if not raw or not raw.strip():
        raise AppError("IMPORT_EMPTY", "文件没有内容", 422)
    if len(raw) > 8 * 1024 * 1024:
        raise AppError("IMPORT_TOO_LARGE", "导入文件不能超过 8 MB", 413)

    text = decode_csv(raw)
    reader = csv.reader(io.StringIO(text))
    records = [row for row in reader if any(cell.strip() for cell in row)]
    records = [row for row in records if not row[0].lstrip().startswith("#")]
    if not records:
        raise AppError("IMPORT_EMPTY", "CSV 只有表头或注释，没有数据行", 422)

    canonical: list[str] = []
    for cell in records[0]:
        key = cell.strip().lower()
        field = columns.get(key)
        if field is None:
            raise AppError(
                "IMPORT_COLUMN_UNKNOWN",
                f"无法识别的列「{cell.strip()}」，可用列：{'、'.join(sorted(set(columns.values())))}",
                422,
            )
        canonical.append(field)

    missing = [field for field in required if field not in canonical]
    if missing:
        raise AppError(
            "IMPORT_COLUMN_MISSING",
            f"缺少必需列：{'、'.join(missing)}",
            422,
        )

    rows: list[dict[str, str]] = []
    for record in records[1:]:
        if len(rows) >= max_rows:
            raise AppError(
                "IMPORT_TOO_MANY_ROWS",
                f"单次最多导入 {max_rows} 行，请分批",
                422,
            )
        row: dict[str, str] = {}
        for index, field in enumerate(canonical):
            if field in row:
                # 同一列出现两次时保留第一个，避免后者覆盖前者造成难以察觉的数据错位
                continue
            row[field] = record[index].strip() if index < len(record) else ""
        rows.append(row)
    return rows


def split_ids(value: str | None) -> list[int]:
    """把 ``1,2、3;4`` 这类写法解析成 id 列表；无法解析的片段直接跳过。"""
    if not value:
        return []
    cleaned = value.replace("，", ",").replace("、", ",").replace(";", ",").replace("|", ",")
    result: list[int] = []
    for part in cleaned.replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            number = int(part)
        except ValueError:
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def optional_int(value: str | None) -> int | None:
    """空串与 None 都归一到 ``None``，其余交给 pydantic 报字段级错误。"""
    if value is None or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError:
        raise AppError("IMPORT_ROW_INVALID", f"「{value.strip()}」不是有效的数字", 422) from None


def optional_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()
