from datetime import timedelta

import pytest

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)
    assert not verify_password("password", "not-a-valid-hash")


def test_access_token_round_trip(settings: Settings) -> None:
    token = create_access_token("user-123", settings, additional_claims={"role": "editor"})
    payload = decode_access_token(token, settings)

    assert payload["sub"] == "user-123"
    assert payload["role"] == "editor"


@pytest.mark.parametrize("token", ["invalid", ""])
def test_rejects_malformed_token(token: str, settings: Settings) -> None:
    with pytest.raises(AppError, match="invalid or expired") as error:
        decode_access_token(token, settings)

    assert error.value.status_code == 401
    assert error.value.code == "invalid_token"
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


def test_rejects_expired_token(settings: Settings) -> None:
    token = create_access_token("user-123", settings, expires_delta=timedelta(seconds=-1))

    with pytest.raises(AppError, match="invalid or expired"):
        decode_access_token(token, settings)
