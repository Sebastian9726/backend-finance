"""Contrasenas y tokens."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

# Argon2id. A diferencia de bcrypt no trunca a 72 bytes, asi que una frase de
# paso larga aporta toda su entropia.
_password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hasher.verify(password, password_hash)


# ---------------------------------------------------------------------------
# Access token (JWT, corto, viaja en el header Authorization)
# ---------------------------------------------------------------------------


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Devuelve (token, segundos_de_vida)."""
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> uuid.UUID | None:
    """Devuelve el id del usuario, o None si el token es invalido o expiro."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None

    if payload.get("type") != "access":
        return None
    try:
        return uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Refresh token (opaco, largo, viaja en cookie httpOnly)
# ---------------------------------------------------------------------------


def generate_refresh_token() -> str:
    """Token opaco. No es un JWT: no lleva informacion y solo sirve si esta
    vigente en la base de datos, lo que permite revocarlo de verdad."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Solo el hash se guarda. SHA-256 basta porque el token ya es aleatorio de
    384 bits: no hay nada que adivinar por fuerza bruta, a diferencia de una
    contrasena elegida por una persona."""
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
