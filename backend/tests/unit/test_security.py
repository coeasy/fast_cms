from __future__ import annotations

import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    new_session_id,
    validate_password_strength,
    verify_login_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("Secret12345")
    assert hashed.startswith("$argon2id$")
    assert verify_password("Secret12345", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_password_strength_rules() -> None:
    assert validate_password_strength("short1")[0] is False
    assert validate_password_strength("onlyletters")[0] is False
    assert validate_password_strength("12345678")[0] is False
    assert validate_password_strength("Admin12345", username="admin12345")[0] is False
    assert validate_password_strength("Admin12345", username="root") == (True, "")


def test_password_strength_rejects_common_and_respects_policy_length() -> None:
    assert validate_password_strength("Password1")[0] is False
    assert validate_password_strength("Qwerty123")[0] is False
    # 黑名单只拦截完全相同的小写形式，加后缀的强口令仍然可用
    assert validate_password_strength("Password1-x")[0] is True
    rejected = validate_password_strength("Abc12345", min_length=12)
    assert rejected[0] is False
    assert "12" in rejected[1]
    # 配置低于安全下限时按下限执行，避免出现 4 位这种策略
    assert validate_password_strength("Abc12345", min_length=4)[0] is True
    assert validate_password_strength("Abc12", min_length=4)[0] is False


def test_unknown_account_verification_does_not_raise() -> None:
    assert verify_login_password("anything", None) is False
    assert verify_login_password("anything", hash_password("Something12345")) is False
    assert verify_login_password("Something12345", hash_password("Something12345")) is True


def test_access_token_claims() -> None:
    token = create_access_token(7, "session-abc", 3)
    payload = decode_access_token(token)
    assert payload["sub"] == "7"
    assert payload["sid"] == "session-abc"
    assert payload["ver"] == 3
    assert payload["iss"] == "fastcms"


def test_tampered_access_token_rejected() -> None:
    token = create_access_token(1, "sid", 1)
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token[:-2] + "aa")


def test_refresh_token_and_session_id_are_random() -> None:
    first = new_refresh_token()
    second = new_refresh_token()
    assert first != second
    assert len(first) > 40
    assert len(hash_token(first)) == 64
    assert new_session_id() != new_session_id()
