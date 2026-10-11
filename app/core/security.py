"""Helpers de seguridad: hash de contraseñas (argon2), access token JWT y refresh token opaco.

Diseño en docs/adr/0004-auth-propia.md y en modelo-de-datos.md (D11, D12).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()

# Hash de una contraseña que nadie conoce. El login lo verifica cuando el correo no existe, así
# la respuesta tarda lo mismo y no revela qué correos están registrados (F1.4).
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

ACCESS_TOKEN_TYPE = "access"  # noqa: S105


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


def create_access_token(user_id: int, session_id: str) -> str:
    """JWT corto con el id de la sesión (`sid`), que la API valida en cada petición (F1.6)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sid": session_id,
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Valida firma, expiración, tipo y claims. Lanza jwt.InvalidTokenError si algo no cuadra."""
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "sid", "exp", "iat"]},
    )
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("tipo de token inesperado")
    return payload


def new_refresh_token() -> str:
    """Refresh token opaco: 32 bytes aleatorios. En la base solo se guarda su SHA-256."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> bytes:
    # El token es aleatorio de 256 bits: SHA-256 basta, no hace falta un hash lento (D12).
    return hashlib.sha256(token.encode()).digest()
