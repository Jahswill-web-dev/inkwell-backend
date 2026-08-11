from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings
from app.core.exceptions import AppError

password_hash = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hash.hash("inkwell-dummy-password-for-constant-work-verification")


def hash_password(password: str) -> str:
    """Hash a plaintext password using the recommended Argon2 configuration."""

    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plaintext password without exposing backend hash errors."""

    valid, _updated_hash = verify_and_update_password(password, hashed_password)
    return valid


def verify_and_update_password(password: str, hashed_password: str) -> tuple[bool, str | None]:
    """Verify a password and return a replacement hash when its parameters are outdated."""

    try:
        return password_hash.verify_and_update(password, hashed_password)
    except Exception:
        return False, None


def create_access_token(
    subject: str,
    settings: Settings,
    *,
    expires_delta: timedelta | None = None,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    expires = now + (expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes))
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": expires,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    if additional_claims:
        reserved = {"sub", "iat", "exp", "iss", "aud"}
        payload.update(
            {key: value for key, value in additional_claims.items() if key not in reserved}
        )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AppError(
            status_code=401,
            code="invalid_token",
            message="The access token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return payload
