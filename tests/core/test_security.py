from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("clave-segura-123")
    assert hashed != "clave-segura-123"
    assert hashed.startswith("$argon2")
    assert verify_password("clave-segura-123", hashed)
    assert not verify_password("otra-clave", hashed)
    assert not verify_password("clave-segura-123", "no-es-un-hash")


def test_access_token_roundtrip() -> None:
    token = create_access_token(42, "3f1c0a52-8f0e-4d1e-9d7a-2c3b4a5d6e7f")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["sid"] == "3f1c0a52-8f0e-4d1e-9d7a-2c3b4a5d6e7f"


def _firmar(payload: dict) -> str:
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def test_access_token_rejects_wrong_type_missing_sid_and_expired() -> None:
    now = datetime.now(UTC)
    base = {"sub": "42", "sid": "x", "iat": now, "exp": now + timedelta(minutes=5)}
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(_firmar({**base, "type": "refresh"}))
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(
            _firmar({"sub": "42", "type": "access", "iat": now, "exp": base["exp"]})
        )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(_firmar({**base, "type": "access", "exp": now - timedelta(seconds=1)}))
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(create_access_token(42, "x") + "a")


def test_refresh_token_is_random_and_hashed_to_32_bytes() -> None:
    a, b = new_refresh_token(), new_refresh_token()
    assert a != b
    assert len(hash_refresh_token(a)) == 32
    assert hash_refresh_token(a) == hash_refresh_token(a)
