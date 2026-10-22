from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.modules.notify import channels


def test_mask_webhook_url_hides_token_query_and_long_segments() -> None:
    masked = channels.mask_webhook_url(
        "https://oapi.dingtalk.com/robot/send?access_token=abcdef0123456789&timestamp=1"
    )
    assert "abcdef0123456789" not in masked
    assert "access_token=abc" in masked
    assert "oapi.dingtalk.com" in masked

    hooked = channels.mask_webhook_url("https://hooks.example.com/services/A1B2C3D4E5F6G7H8I9J0K/L")
    assert "A1B2C3D4E5F6G7H8I9J0K" not in hooked


def test_validate_webhook_url_rejects_unsafe_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "notify_allow_private_target", False)
    for url in (
        "http://127.0.0.1/hook",
        "https://192.168.1.10/hook",
        "https://[::1]/hook",
        "https://user:pass@example.com/hook",
        "https://example.com/hook#frag",
        "https://example.com:8443/hook",
        "ftp://example.com/hook",
        "",
    ):
        with pytest.raises(AppError) as excinfo:
            channels.validate_webhook_url(url)
        assert excinfo.value.code == "NOTIFY_URL_INVALID"


def test_validate_webhook_url_allows_local_target_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "notify_allow_private_target", True)
    assert channels.validate_webhook_url("http://127.0.0.1:8000/hook") == "http://127.0.0.1:8000/hook"


def test_dingtalk_signature_matches_documented_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.time, "time", lambda: 1700000000.0)
    secret = "SECtest123"
    signed = channels._signed_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=tok", secret)
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(signed).query))
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), f"1700000000000\n{secret}".encode(), hashlib.sha256).digest()
    ).decode("utf-8")
    assert query["timestamp"] == "1700000000000"
    assert query["sign"] == expected


def test_dingtalk_signature_uses_question_mark_without_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.time, "time", lambda: 1700000000.0)
    signed = channels._signed_dingtalk("https://example.com/hook", "secret")
    assert "?timestamp=" in signed and "&sign=" in signed


def test_feishu_signature_uses_timestamp_and_secret_as_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.time, "time", lambda: 1700000000.0)
    timestamp, sign = channels._signed_feishu("secret")
    expected = base64.b64encode(
        hmac.new(f"{timestamp}\nsecret".encode(), b"", hashlib.sha256).digest()
    ).decode("utf-8")
    assert timestamp == "1700000000"
    assert sign == expected


def test_bodies_match_each_platform_shape() -> None:
    assert channels._markdown_body("dingtalk", "T", "C")["markdown"]["title"] == "T"
    assert "content" in channels._markdown_body("wecom", "T", "C")["markdown"]
    assert channels._markdown_body("feishu", "T", "C")["msg_type"] == "text"
    assert channels._markdown_body("webhook", "T", "C")["title"] == "T"


def test_judge_reads_platform_return_codes() -> None:
    class Fake:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"errcode": 31000, "errmsg": "keywords not in content"}

        text = '{"errcode":31000,"errmsg":"keywords not in content"}'

    result = channels._judge("dingtalk", Fake())
    assert result.ok is False
    assert "keywords not in content" in (result.error or "")

    class Generic:
        status_code = 202

        text = ""

        @staticmethod
        def json() -> dict:
            raise ValueError("no json")

    assert channels._judge("webhook", Generic()).ok is True


async def test_send_rejects_wecom_secret_without_network() -> None:
    result = await channels.send_markdown(
        channel_type="wecom",
        webhook_url="https://example.com/hook",
        secret="unused",
        title="T",
        content="C",
        event="test",
        dedupe_key="k",
    )
    assert result.ok is False
    assert "企业微信" in (result.error or "")
