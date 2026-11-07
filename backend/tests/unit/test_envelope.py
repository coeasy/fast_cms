"""响应信封（``core.envelope``）的契约行为。

这些断言锁住两件事：

1. 运行时 ``success()`` 的 payload 能被信封模型原样接住（字段不多不少）；
2. 可选字段「没设置」与「显式 null」在序列化后仍然可区分——列表的 ``total``
   在 ``total=false`` 时必须**不出现**，而不是变成 ``null``。否则前端与契约
   都要跟着改，且「跳过 count」这个优化在响应里看不出来。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.envelope import Envelope, KeysetPage, Page
from app.core.errors import success
from app.kernel.schemas import RoleBrief


def test_envelope_accepts_success_payload() -> None:
    payload = success({"count": 3}, "已处理")
    envelope = Envelope[dict[str, int]].model_validate(payload)
    assert envelope.code == "OK"
    assert envelope.data == {"count": 3}
    assert envelope.message == "已处理"


def test_envelope_keeps_request_id_from_payload() -> None:
    envelope = Envelope[None].model_validate(success(None))
    assert envelope.data is None
    # 未设置请求上下文时 request_id 为空串，而不是校验失败
    assert envelope.request_id == ""


def test_page_exclude_unset_keeps_total_absent() -> None:
    page = Page[RoleBrief].model_validate({"items": [{"id": 1, "name": "运维", "code": "ops"}], "page": 1, "page_size": 20})
    dumped = page.model_dump(exclude_unset=True)
    assert "total" not in dumped
    assert dumped["items"][0]["code"] == "ops"


def test_page_exclude_unset_keeps_explicit_null() -> None:
    page = Page[RoleBrief].model_validate({"items": [], "page": 1, "page_size": 20, "total": None})
    assert "total" in page.model_dump(exclude_unset=True)


def test_keyset_page_carries_cursor() -> None:
    page = KeysetPage[RoleBrief].model_validate(
        {"items": [], "page": 1, "page_size": 20, "next_cursor": 42}
    )
    assert page.next_cursor == 42


def test_page_rejects_wrong_item_shape() -> None:
    with pytest.raises(ValidationError):
        Page[RoleBrief].model_validate({"items": [{"id": 1}], "page": 1, "page_size": 20})
