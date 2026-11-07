"""通知渠道密钥的静态加密。

威胁模型：``sys_notify_channel.secret`` 存的是第三方推送签名密钥（DingTalk / Feishu /
WeCom / webhook 的 access_token 或签名），一旦数据库或备份泄露，攻击者即可伪造该渠道
发出的请求。因此把 secret 加密落库，密钥由应用 ``secret_key`` 派生。

组合策略（P0-3）：

- **派生**：用 HMAC-SHA256 以 ``secret_key`` 为种子、按用途（``fastcms:crypto:enc`` /
  ``fastcms:crypto:mac``）域分离派生两个独立 32 字节密钥，避免与 JWT 签名密钥混用；
- **加密**：``Encrypt-then-MAC`` —— 随机 16 字节 nonce 生成 HMAC-SHA256 计数器模式的
  密钥流，与明文 XOR 得密文，再用独立 MAC 密钥对 ``nonce || ciphertext`` 计算认证标签；
  校验不通过直接拒绝，不泄露任何明文信息；
- **格式**：``enc:v1:<base64url(nonce)>.<base64url(tag)>.<base64url(ciphertext)>``，
  版本前缀 ``enc:v1`` 便于未来轮换算法；
- **旧数据兼容**：不携带 ``enc:v1`` 前缀的值按历史明文返回（解密回退窗口），
  保证迁移期间已入库的明文 secret 仍能正常投递；写入新值一律加密。

说明：环境受限（镜像暂不能安装 ``cryptography``）时使用 HMAC-SHA256 计数器模式 + 
Encrypt-then-MAC 的纯标准库构造；若未来引入 ``cryptography``，应优先改用
AES-GCM / Fernet，并保留 ``enc:v1`` 前缀以做算法演进。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from functools import lru_cache

from app.core.config import settings

PREFIX = "enc:v1"
_NONCE_BYTES = 16
_TAG_BYTES = 32


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _derive(master_key: str, purpose: bytes) -> bytes:
    """域分离派生：HMAC-SHA256(master_key, purpose || length)。"""
    return hmac.new(master_key.encode("utf-8"), purpose, hashlib.sha256).digest()


@lru_cache(maxsize=2)
def _keys(master_key: str) -> tuple[bytes, bytes]:
    enc_key = _derive(master_key, b"fastcms:crypto:enc")
    mac_key = _derive(master_key, b"fastcms:crypto:mac")
    return enc_key, mac_key


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 计数器模式：block = HMAC(enc_key, nonce || counter)。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(f"{PREFIX}:"))


def encrypt_secret(plaintext: str, master_key: str | None = None) -> str:
    if not plaintext:
        return ""
    key = master_key if master_key is not None else settings.secret_key
    enc_key, mac_key = _keys(key)
    nonce = os.urandom(_NONCE_BYTES)
    data = plaintext.encode("utf-8")
    stream = _keystream(enc_key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream, strict=True))
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return f"{PREFIX}:{_b64e(nonce)}.{_b64e(tag)}.{_b64e(ciphertext)}"


def decrypt_secret(value: str | None, master_key: str | None = None) -> str:
    if not value:
        return ""
    if not is_encrypted(value):
        # 旧数据回退窗口：迁移前入库的明文 secret 原样返回
        return value
    key = master_key if master_key is not None else settings.secret_key
    enc_key, mac_key = _keys(key)
    try:
        nonce_b64, tag_b64, cipher_b64 = value[len(PREFIX) + 1 :].split(".")
        nonce = _b64d(nonce_b64)
        tag = _b64d(tag_b64)
        ciphertext = _b64d(cipher_b64)
    except (ValueError, TypeError):
        raise ValueError("密文格式无效，无法解密通知渠道 secret") from None
    if len(nonce) != _NONCE_BYTES or len(tag) != _TAG_BYTES:
        raise ValueError("密文参数长度无效，无法解密通知渠道 secret")
    expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("通知渠道 secret 认证失败：密钥可能已轮换或数据被篡改")
    stream = _keystream(enc_key, nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True))
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("通知渠道 secret 解码失败") from None
