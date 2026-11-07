"""B-8 / 4-2：进程内指标（Prometheus 文本格式）单元测试。"""

from __future__ import annotations

import pytest

from app.core import metrics


def _fresh() -> None:
    metrics.reset()


class TestStatusClass:
    def test_success(self) -> None:
        assert metrics._status_class(200) == "2xx"
        assert metrics._status_class(299) == "2xx"

    def test_redirect(self) -> None:
        assert metrics._status_class(302) == "3xx"

    def test_client_error(self) -> None:
        assert metrics._status_class(404) == "4xx"

    def test_server_error(self) -> None:
        assert metrics._status_class(500) == "5xx"

    def test_out_of_range_falls_back_to_5xx(self) -> None:
        assert metrics._status_class(99) == "5xx"
        assert metrics._status_class(600) == "5xx"


class TestRecordRequest:
    def setup_method(self) -> None:
        _fresh()

    def test_counts_by_method_and_status_class(self) -> None:
        metrics.record_request("GET", 200, 1.0)
        metrics.record_request("POST", 201, 2.0)
        metrics.record_request("GET", 500, 3.0)
        snap = metrics.snapshot()
        assert snap["requests_total"] == {
            ("GET", "2xx"): 1,
            ("POST", "2xx"): 1,
            ("GET", "5xx"): 1,
        }
        assert snap["duration_count"] == 3

    def test_method_is_upper_cased(self) -> None:
        metrics.record_request("get", 200, 1.0)
        snap = metrics.snapshot()
        assert ("GET", "2xx") in snap["requests_total"]

    def test_negative_duration_clamped_to_zero(self) -> None:
        metrics.record_request("GET", 200, -5.0)
        snap = metrics.snapshot()
        assert snap["duration_sum"] == 0.0

    def test_accumulates_duration(self) -> None:
        metrics.record_request("GET", 200, 1500.0)  # 1.5s
        snap = metrics.snapshot()
        assert snap["duration_sum"] == pytest.approx(1.5)
        # 落在 1.0s 与 2.5s 桶之间
        assert snap["latency"].get(1.0, 0) == 0
        assert snap["latency"].get(2.5, 0) == 1


class TestRender:
    def setup_method(self) -> None:
        _fresh()

    def test_render_contains_help_and_type_lines(self) -> None:
        text = metrics.render()
        assert "# HELP fastcms_http_requests_total" in text
        assert "# TYPE fastcms_http_requests_total counter" in text
        assert "fastcms_uptime_seconds" in text

    def test_render_omits_zero_series(self) -> None:
        # 没有任何请求时不应输出空系列
        text = metrics.render()
        assert "fastcms_http_requests_total{" not in text

    def test_render_emits_recorded_series(self) -> None:
        metrics.record_request("GET", 200, 10.0)
        text = metrics.render()
        assert 'fastcms_http_requests_total{method="GET",status="2xx"} 1' in text

    def test_render_has_histogram_with_inf_bucket(self) -> None:
        metrics.record_request("GET", 200, 10.0)
        text = metrics.render()
        assert 'fastcms_http_request_duration_seconds_bucket{le="+Inf"} 1' in text
        assert "fastcms_http_request_duration_seconds_count 1" in text

    def test_render_escapes_label(self) -> None:
        assert metrics._escape_label('a"b') == 'a\\"b'
        assert metrics._escape_label("a\\b") == "a\\\\b"

    def test_render_ends_with_newline(self) -> None:
        assert metrics.render().endswith("\n")
