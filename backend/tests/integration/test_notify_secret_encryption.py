"""P0-3：通知渠道 secret 静态加密存储与解密回退窗口。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import crypto
from app.core.config import settings
from app.db.session import session_scope
from app.modules.ops.notify.models import NotifyChannel
from tests.conftest import auth

API = settings.api_prefix


class TestCryptoPrimitive:
    def test_roundtrip(self) -> None:
        plain = "SECabc123456"
        enc = crypto.encrypt_secret(plain)
        assert crypto.is_encrypted(enc)
        assert enc != plain
        assert crypto.decrypt_secret(enc) == plain

    def test_encrypted_prefix_and_format(self) -> None:
        enc = crypto.encrypt_secret("x")
        assert enc.startswith("enc:v1:")
        # 三段 base64url：nonce.tag.ciphertext
        assert len(enc[len("enc:v1:") :].split(".")) == 3

    def test_non_deterministic_nonce(self) -> None:
        a = crypto.encrypt_secret("same")
        b = crypto.encrypt_secret("same")
        assert a != b  # 随机 nonce：同一明文两次加密不同

    def test_legacy_plaintext_passthrough(self) -> None:
        # 迁移前入库的明文 secret：无 enc:v1 前缀，原样返回（回退窗口）
        assert crypto.decrypt_secret("SECabc123456") == "SECabc123456"
        assert crypto.is_encrypted("SECabc123456") is False

    def test_empty_handling(self) -> None:
        assert crypto.encrypt_secret("") == ""
        assert crypto.decrypt_secret(None) == ""
        assert crypto.decrypt_secret("") == ""

    def test_wrong_key_rejected(self) -> None:
        enc = crypto.encrypt_secret("SECabc", master_key="correct-key")
        with pytest.raises(ValueError, match="认证失败"):
            crypto.decrypt_secret(enc, master_key="wrong-key")

    def test_tampering_detected(self) -> None:
        enc = crypto.encrypt_secret("SECabc123456")
        nonce_b64, tag_b64, cipher_b64 = enc[len("enc:v1:") :].split(".")
        # 篡改密文最后一个字符
        flipped = "0" if cipher_b64[-1] != "0" else "1"
        tampered = f"enc:v1:{nonce_b64}.{tag_b64}.{cipher_b64[:-1]}{flipped}"
        with pytest.raises(ValueError, match="认证失败"):
            crypto.decrypt_secret(tampered)

    def test_unicode_roundtrip(self) -> None:
        plain = "签名密钥-中文-🔐"
        assert crypto.decrypt_secret(crypto.encrypt_secret(plain)) == plain


class TestNotifySecretAtRest:
    def _create(self, client: TestClient, token: str, secret: str) -> dict[str, Any]:
        response = client.post(
            f"{API}/notify/channels",
            headers=auth(token),
            json={
                "name": f"加密渠道-{id(self)}",
                "channel_type": "webhook",
                "webhook_url": "https://example.com/hook?token=abc123",
                "secret": secret,
                "events": ["job.failed"],
                "status": "enabled",
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    async def _raw_secret(self, channel_id: int) -> str | None:
        async with session_scope() as db:
            row = await db.get(NotifyChannel, channel_id)
            assert row is not None
            return row.secret

    async def _delete_channel(self, channel_id: int) -> None:
        async with session_scope() as db:
            row = await db.get(NotifyChannel, channel_id)
            if row is not None:
                await db.delete(row)

    async def test_db_never_stores_plaintext_secret(
        self, client: TestClient, admin_token: str
    ) -> None:
        channel = self._create(client, admin_token, "SECabc123456")
        try:
            raw = await self._raw_secret(channel["id"])
            assert raw is not None
            # DB 落的是密文：不是明文、可识别为加密格式、可解回原值
            assert raw != "SECabc123456"
            assert crypto.is_encrypted(raw)
            assert crypto.decrypt_secret(raw) == "SECabc123456"
        finally:
            await self._delete_channel(channel["id"])

    async def test_update_secret_encrypts_and_clear_removes(
        self, client: TestClient, admin_token: str
    ) -> None:
        channel = self._create(client, admin_token, "OLDsecret123")
        try:
            raw = await self._raw_secret(channel["id"])
            assert crypto.decrypt_secret(raw) == "OLDsecret123"

            updated = client.put(
                f"{API}/notify/channels/{channel['id']}",
                headers=auth(admin_token),
                json={"secret": "NEWsecret456"},
            )
            assert updated.status_code == 200, updated.text
            raw_new = await self._raw_secret(channel["id"])
            assert crypto.decrypt_secret(raw_new) == "NEWsecret456"
            assert crypto.is_encrypted(raw_new)

            cleared = client.put(
                f"{API}/notify/channels/{channel['id']}",
                headers=auth(admin_token),
                json={"secret": ""},
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["data"]["has_secret"] is False
            assert await self._raw_secret(channel["id"]) is None
        finally:
            await self._delete_channel(channel["id"])

    async def test_legacy_plaintext_secret_still_delivers(
        self, client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 模拟迁移前已入库的明文 secret：直接写库绕过加密
        async with session_scope() as db:
            legacy = NotifyChannel(
                name=f"明文渠道-{id(self)}",
                channel_type="webhook",
                webhook_url="https://example.com/hook?token=abc123",
                secret="LEGACYplain",
                events=["job.failed"],
                status="enabled",
            )
            db.add(legacy)
            await db.commit()
            await db.refresh(legacy)
            legacy_id = int(legacy.id)

        sent: dict[str, Any] = {}

        async def capture(**kwargs: Any) -> Any:
            from app.modules.ops.notify import channels as ch

            sent.update(kwargs)
            return ch.SendResult(True, None, 200)

        from app.modules.ops.notify import channels as ch_mod

        monkeypatch.setattr(ch_mod, "send_markdown", capture)
        try:
            # 渠道测试端点同步投递：走 decrypt_secret 的明文回退窗口
            tested = client.post(f"{API}/notify/channels/{legacy_id}/test", headers=auth(admin_token))
            assert tested.status_code == 200, tested.text
            assert sent.get("secret") == "LEGACYplain"
        finally:
            await self._delete_channel(legacy_id)
