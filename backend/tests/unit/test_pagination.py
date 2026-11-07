"""P1-5：分页契约（total 可选 + keyset 游标）单元测试。"""

from __future__ import annotations

import pytest

from app.core.pagination import PageParams, _assert_unique_key, page_data


class TestPageParams:
    def test_offset(self) -> None:
        params = PageParams(page=3, page_size=20)
        assert params.offset == 40

    def test_total_can_be_disabled(self) -> None:
        params = PageParams(total=False)
        assert params.total is False

    def test_total_can_be_enabled_explicitly(self) -> None:
        params = PageParams(total=True)
        assert params.total is True


class TestPageData:
    def test_includes_total_when_counted(self) -> None:
        params = PageParams(page=1, page_size=20)
        data = page_data([{"id": 1}], total=5, params=params)
        assert data["total"] == 5
        assert data["items"] == [{"id": 1}]
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_omits_total_when_not_counted(self) -> None:
        # total=false 时调用方返回 None，响应不应携带 total 字段
        params = PageParams(total=False)
        data = page_data([{"id": 1}], total=None, params=params)
        assert "total" not in data
        assert data["items"] == [{"id": 1}]


class TestKeysetUniqueKey:
    """B-9 / 3-4：keyset 分页键必须唯一，复用非唯一键立刻报错。"""

    def test_primary_key_is_accepted(self) -> None:
        from app.kernel.models import User

        _assert_unique_key(User.id)  # 主键，合法

    def test_unique_column_is_accepted(self) -> None:
        from app.kernel.models import User

        _assert_unique_key(User.username_normalized)  # 唯一索引列，合法

    def test_non_unique_column_is_rejected(self) -> None:
        from app.kernel.models import User

        with pytest.raises(ValueError):
            _assert_unique_key(User.username)  # 普通列，必须拒绝

    def test_expression_is_rejected(self) -> None:
        from sqlalchemy import func

        from app.kernel.models import User

        with pytest.raises(ValueError):
            _assert_unique_key(func.lower(User.username))  # 无法证明唯一，拒绝
