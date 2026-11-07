"""纯格式化工具：只做「值 → 值」的转换，不认识任何 ORM 实体。

与 ``app/kernel/serializers.py`` 的分工（这两个同名文件曾多次被搞混）：

| | ``app/core/serializers.py`` | ``app/kernel/serializers.py`` |
|---|---|---|
| 输入 | 标量（``datetime`` 等） | ORM 实体（``User`` / ``Role`` / ``UserSession``） |
| 输出 | 标量（ISO 字符串） | 可直接进响应模型的 dict |
| 职责 | **纯格式化**：时区归一、精度截断 | **实体投影**：挑字段、拼关联、按权限置空 |
| 依赖 | 无（只用标准库） | ``app.kernel.models`` + 本模块 |
| 典型调用 | ``iso(row.created_at)`` | ``user_out(user, include_roles=False)`` |

判据很简单：**需要拿到整行实体才能决定输出什么 → 属于 kernel；只处理手上这一个值 →
属于 core**。放在 core 的好处是模块层可以放心 import 而不引入 ORM 依赖。
"""

from __future__ import annotations

from datetime import datetime


def iso(value: datetime | None) -> str | None:
    """把 ``datetime`` 渲染成秒级 ISO-8601（UTC 以 ``Z`` 结尾）。

    库里存的是 naive UTC，前端 ``new Date()`` 需要带时区标识才会按本地时区显示，
    否则 ``2026-11-01T08:00:00`` 会被当成浏览器本地时间，差出 8 小时。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat(timespec="seconds") + "Z"
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
