from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.models.user import User
from app.schemas.auth import AuthResponse, LoginRequest, PublicUser, RegisterRequest


def test_register_request_normalizes_identity_without_changing_password() -> None:
    request = RegisterRequest(
        email="  Writer@Example.COM ",
        username="  Writer_01 ",
        password="  password with spaces  ",
    )

    assert str(request.email) == "writer@example.com"
    assert request.username == "writer_01"
    assert request.password.get_secret_value() == "  password with spaces  "


@pytest.mark.parametrize("username", ["ab", "a" * 31, "writer-name", "writer name"])
def test_register_request_rejects_invalid_usernames(username: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="writer@example.com", username=username, password="password123")


@pytest.mark.parametrize("password", ["short7", "x" * 129])
def test_register_request_rejects_invalid_password_lengths(password: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="writer@example.com", username="writer_01", password=password)


def test_register_request_accepts_password_boundaries() -> None:
    for password in ("x" * 8, "x" * 128):
        request = RegisterRequest(
            email="writer@example.com", username="writer_01", password=password
        )
        assert request.password.get_secret_value() == password


def test_registration_response_exposes_only_public_user_fields() -> None:
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        email="writer@example.com",
        username="writer_01",
        password_hash="secret-hash",
        created_at=now,
        updated_at=now,
    )

    response = AuthResponse(
        access_token="token",
        user=PublicUser.model_validate(user),
    ).model_dump(mode="json")

    assert response["token_type"] == "bearer"
    assert response["user"]["email"] == "writer@example.com"
    assert "password" not in response["user"]
    assert "password_hash" not in response["user"]


def test_login_request_normalizes_email_without_changing_password() -> None:
    request = LoginRequest(email="  Writer@Example.COM ", password=" password ")

    assert str(request.email) == "writer@example.com"
    assert request.password.get_secret_value() == " password "


@pytest.mark.parametrize("password", ["", "x" * 129])
def test_login_request_rejects_invalid_password_lengths(password: str) -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="writer@example.com", password=password)
