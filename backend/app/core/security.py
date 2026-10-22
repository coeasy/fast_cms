from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings
from app.db.base import utcnow

ALGORITHM = "HS256"
ISSUER = "fastcms"
AUDIENCE = "fastcms-admin"

password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

MIN_PASSWORD_LENGTH = 8
# 常见弱口令与小写包含关系检查，命中即拒绝（来自 OWASP  top-10k 的高频项）
WEAK_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "qwerty123",
        "abc123456",
        "a123456789",
        "123456789",
        "12345678",
        "11111111",
        "00000000",
        "iloveyou",
        "letmein1",
        "admin888",
        "root1234",
        "test1234",
        "88888888",
        "1qaz2wsx",
        "welcome123",
        "change_me1",
    }
)

# 用户名不存在时也要做一次 Argon2 验证，否则响应时间直接暴露“这个账号存在”
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(24))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def verify_login_password(password: str, password_hash: str | None) -> bool:
    """账号不存在时也执行一次 Argon2 校验，让成功/失败/未知用户的响应时间保持一致。"""
    return verify_password(password, password_hash or DUMMY_PASSWORD_HASH)


def validate_password_strength(
    password: str, username: str = "", *, min_length: int = MIN_PASSWORD_LENGTH
) -> tuple[bool, str]:
    floor = max(MIN_PASSWORD_LENGTH, min(int(min_length or 0), 64))
    if len(password) < floor:
        return False, f"密码至少 {floor} 位"
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return False, "密码必须同时包含字母和数字"
    if username and username.lower() in password.lower():
        return False, "密码不能包含用户名"
    if password.lower() in WEAK_PASSWORDS:
        return False, "密码过于常见，请更换"
    return True, ""


def create_access_token(user_id: int, session_id: str, token_version: int) -> str:
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": session_id,
        "ver": token_version,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.auth_access_ttl_min),
        "jti": secrets.token_urlsafe(12),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[ALGORITHM],
        issuer=ISSUER,
        audience=AUDIENCE,
        options={"require": ["exp", "iat", "sub", "sid", "iss"]},
    )


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_id() -> str:
    return secrets.token_hex(16)
