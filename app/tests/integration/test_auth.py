from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from alembic import command
from app.core.config import Settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    decode_access_token,
    verify_password,
)
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.user import User
from app.db.repositories.user import UserRepository
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.services.login_rate_limiter import LoginRateLimiter
from app.tests.integration.test_database import alembic_config, require_test_url

pytestmark = pytest.mark.database


async def clear_users(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            await session.execute(delete(LoginRateLimit))
            await session.execute(delete(User))
    finally:
        await engine.dispose()


async def find_user(settings: Settings, email: str) -> User | None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.scalars(select(User).where(User.email == email))
            return result.first()
    finally:
        await engine.dispose()


async def expire_login_limits(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            await session.execute(
                update(LoginRateLimit).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
    finally:
        await engine.dispose()


async def record_concurrent_failures(settings: Settings, count: int) -> list[LoginRateLimit]:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    limiter = LoginRateLimiter(factory, settings)
    try:
        await asyncio.gather(
            *(
                limiter.record_failure(email="target@example.com", client_ip="192.0.2.10")
                for _ in range(count)
            )
        )
        async with factory() as session:
            result = await session.scalars(select(LoginRateLimit))
            return list(result.all())
    finally:
        await engine.dispose()


@pytest.fixture
def auth_context(settings: Settings) -> Iterator[tuple[TestClient, Settings]]:
    database_url = require_test_url(settings)
    command.upgrade(alembic_config(database_url), "head")
    test_settings = settings.model_copy(
        update={
            "database_url": database_url,
            "cors_origins": ["http://localhost:3000"],
            "login_rate_limit_email_failures": 2,
            "login_rate_limit_ip_failures": 3,
        }
    )
    asyncio.run(clear_users(test_settings))

    with TestClient(create_app(test_settings)) as client:
        yield client, test_settings


def registration_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "email": "writer@example.com",
        "username": "writer_01",
        "password": "correct horse battery staple",
    }
    payload.update(overrides)
    return payload


def login_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "email": "writer@example.com",
        "password": "correct horse battery staple",
    }
    payload.update(overrides)
    return payload


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_registration_creates_normalized_user_and_access_token(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, settings = auth_context
    plaintext_password = "  correct horse battery staple  "

    response = client.post(
        "/api/v1/auth/register",
        json=registration_payload(
            email="  Writer@Example.COM ",
            username="  Writer_01 ",
            password=plaintext_password,
        ),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "writer@example.com"
    assert body["user"]["username"] == "writer_01"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]

    claims = decode_access_token(body["access_token"], settings)
    assert claims["sub"] == body["user"]["id"]

    persisted = asyncio.run(find_user(settings, "writer@example.com"))
    assert persisted is not None
    assert persisted.password_hash != plaintext_password
    assert verify_password(plaintext_password, persisted.password_hash)


@pytest.mark.parametrize(
    ("first_payload", "second_payload", "error_code"),
    [
        (
            registration_payload(),
            registration_payload(email="WRITER@EXAMPLE.COM", username="different_user"),
            "email_already_registered",
        ),
        (
            registration_payload(),
            registration_payload(email="other@example.com", username="WRITER_01"),
            "username_taken",
        ),
    ],
)
def test_registration_rejects_case_insensitive_identity_conflicts(
    auth_context: tuple[TestClient, Settings],
    first_payload: dict[str, str],
    second_payload: dict[str, str],
    error_code: str,
) -> None:
    client, _settings = auth_context
    assert client.post("/api/v1/auth/register", json=first_payload).status_code == 201

    response = client.post("/api/v1/auth/register", json=second_payload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == error_code


@pytest.mark.parametrize(
    ("conflicting_payload", "error_code"),
    [
        (
            registration_payload(username="different_user"),
            "email_already_registered",
        ),
        (
            registration_payload(email="other@example.com"),
            "username_taken",
        ),
    ],
)
def test_database_uniqueness_races_are_returned_as_conflicts(
    auth_context: tuple[TestClient, Settings],
    monkeypatch: pytest.MonkeyPatch,
    conflicting_payload: dict[str, str],
    error_code: str,
) -> None:
    client, _settings = auth_context
    assert client.post("/api/v1/auth/register", json=registration_payload()).status_code == 201

    async def no_match(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(UserRepository, "get_by_email", no_match)
    monkeypatch.setattr(UserRepository, "get_by_username", no_match)

    response = client.post("/api/v1/auth/register", json=conflicting_payload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == error_code


@pytest.mark.parametrize(
    "payload",
    [
        registration_payload(email="not-an-email"),
        registration_payload(username="invalid-name"),
        registration_payload(password="short7"),
        registration_payload(password="x" * 129),
    ],
)
def test_registration_rejects_invalid_input(
    auth_context: tuple[TestClient, Settings], payload: dict[str, str]
) -> None:
    client, _settings = auth_context

    response = client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_login_returns_public_user_and_valid_access_token(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, settings = auth_context
    registration = client.post("/api/v1/auth/register", json=registration_payload())
    assert registration.status_code == 201

    response = client.post(
        "/api/v1/auth/login",
        json=login_payload(email="  WRITER@EXAMPLE.COM "),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == registration.json()["user"]
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert decode_access_token(body["access_token"], settings)["sub"] == body["user"]["id"]


@pytest.mark.parametrize(
    "payload",
    [
        login_payload(password="incorrect password"),
        login_payload(email="missing@example.com"),
    ],
)
def test_login_uses_generic_invalid_credentials_error(
    auth_context: tuple[TestClient, Settings], payload: dict[str, str]
) -> None:
    client, _settings = auth_context
    assert client.post("/api/v1/auth/register", json=registration_payload()).status_code == 201

    response = client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "invalid_credentials",
            "message": "Invalid email or password",
        }
    }


def test_login_verifies_missing_users_against_dummy_hash(
    auth_context: tuple[TestClient, Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.auth_service as auth_service_module

    client, _settings = auth_context
    checked_hashes: list[str] = []

    def capture_verification(_password: str, stored_hash: str) -> tuple[bool, None]:
        checked_hashes.append(stored_hash)
        return False, None

    monkeypatch.setattr(auth_service_module, "verify_and_update_password", capture_verification)

    response = client.post(
        "/api/v1/auth/login",
        json=login_payload(email="missing@example.com"),
    )

    assert response.status_code == 401
    assert checked_hashes == [DUMMY_PASSWORD_HASH]


def test_login_persists_recommended_password_rehash(
    auth_context: tuple[TestClient, Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.auth_service as auth_service_module

    client, settings = auth_context
    assert client.post("/api/v1/auth/register", json=registration_payload()).status_code == 201

    def require_rehash(_password: str, _stored_hash: str) -> tuple[bool, str]:
        return True, "upgraded-password-hash"

    monkeypatch.setattr(auth_service_module, "verify_and_update_password", require_rehash)

    response = client.post("/api/v1/auth/login", json=login_payload())

    assert response.status_code == 200
    persisted = asyncio.run(find_user(settings, "writer@example.com"))
    assert persisted is not None
    assert persisted.password_hash == "upgraded-password-hash"


def test_login_rate_limits_repeated_failures_by_email(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = auth_context
    assert client.post("/api/v1/auth/register", json=registration_payload()).status_code == 201

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login", json=login_payload(password="incorrect password")
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/v1/auth/login",
        json=login_payload(password="incorrect password"),
        headers={"Origin": "http://localhost:3000"},
    )

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "Retry-After" in blocked.headers["Access-Control-Expose-Headers"]
    assert blocked.json()["error"]["code"] == "too_many_login_attempts"


def test_login_rate_limits_failures_across_emails_by_ip(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = auth_context

    for attempt in range(3):
        response = client.post(
            "/api/v1/auth/login",
            json=login_payload(email=f"missing{attempt}@example.com"),
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/v1/auth/login",
        json=login_payload(email="another-missing@example.com"),
    )

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_login_attempts"


def test_successful_login_clears_email_failures_but_not_ip_failures(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = auth_context
    assert client.post("/api/v1/auth/register", json=registration_payload()).status_code == 201

    wrong_password = login_payload(password="incorrect password")
    assert client.post("/api/v1/auth/login", json=wrong_password).status_code == 401
    assert client.post("/api/v1/auth/login", json=login_payload()).status_code == 200
    assert client.post("/api/v1/auth/login", json=wrong_password).status_code == 401
    assert client.post("/api/v1/auth/login", json=wrong_password).status_code == 401

    blocked_by_ip = client.post("/api/v1/auth/login", json=wrong_password)
    assert blocked_by_ip.status_code == 429


def test_expired_login_rate_limit_window_resets(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, settings = auth_context
    wrong_password = login_payload(email="missing@example.com")
    assert client.post("/api/v1/auth/login", json=wrong_password).status_code == 401
    assert client.post("/api/v1/auth/login", json=wrong_password).status_code == 401

    asyncio.run(expire_login_limits(settings))

    assert client.post("/api/v1/auth/login", json=wrong_password).status_code == 401


def test_login_rate_limit_increments_are_atomic_and_keys_are_hashed(
    auth_context: tuple[TestClient, Settings],
) -> None:
    _client, settings = auth_context

    counters = asyncio.run(record_concurrent_failures(settings, 8))

    assert len(counters) == 2
    assert {counter.failure_count for counter in counters} == {8}
    assert all(counter.key_hash not in {"target@example.com", "192.0.2.10"} for counter in counters)


@pytest.mark.parametrize(
    "payload",
    [
        login_payload(email="not-an-email"),
        login_payload(password=""),
        login_payload(password="x" * 129),
    ],
)
def test_login_rejects_invalid_input(
    auth_context: tuple[TestClient, Settings], payload: dict[str, str]
) -> None:
    client, _settings = auth_context

    response = client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_me_accepts_tokens_from_registration_and_login(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = auth_context
    registration = client.post("/api/v1/auth/register", json=registration_payload())
    assert registration.status_code == 201

    login = client.post("/api/v1/auth/login", json=login_payload())
    assert login.status_code == 200

    for token_response in (registration, login):
        response = client.get(
            "/api/v1/auth/me",
            headers=bearer_headers(token_response.json()["access_token"]),
        )
        assert response.status_code == 200
        assert response.json() == registration.json()["user"]
        assert "password" not in response.json()
        assert "password_hash" not in response.json()


@pytest.mark.parametrize("authorization", [None, "Basic credentials", "Digest credentials"])
def test_me_requires_bearer_authentication(
    auth_context: tuple[TestClient, Settings], authorization: str | None
) -> None:
    client, _settings = auth_context
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication is required",
        }
    }


def test_me_rejects_every_invalid_token_variant(
    auth_context: tuple[TestClient, Settings],
) -> None:
    client, settings = auth_context
    registration = client.post("/api/v1/auth/register", json=registration_payload())
    assert registration.status_code == 201
    user_id = registration.json()["user"]["id"]
    now = datetime.now(UTC)

    invalid_tokens = [
        "not-a-jwt",
        create_access_token(user_id, settings, expires_delta=timedelta(seconds=-1)),
        create_access_token("not-a-uuid", settings),
        create_access_token(str(uuid4()), settings),
        create_access_token(
            user_id,
            settings.model_copy(update={"jwt_issuer": "unexpected-issuer"}),
        ),
        create_access_token(
            user_id,
            settings.model_copy(update={"jwt_audience": "unexpected-audience"}),
        ),
        jwt.encode(
            {
                "sub": user_id,
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        ),
    ]

    for token in invalid_tokens:
        response = client.get("/api/v1/auth/me", headers=bearer_headers(token))
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json() == {
            "error": {
                "code": "invalid_token",
                "message": "The access token is invalid or expired",
            }
        }
