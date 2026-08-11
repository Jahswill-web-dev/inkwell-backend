import pytest
from pydantic import ValidationError

from app.core.config import Settings


def valid_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@localhost/database",
        "jwt_secret_key": "a-secure-test-secret-with-32-characters",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_normalizes_api_prefix() -> None:
    assert valid_settings(api_v1_prefix="v2/").api_v1_prefix == "/v2"


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///test.db",
        "postgresql+asyncpg://user:pass@localhost/database",
        "not-a-url",
    ],
)
def test_rejects_unsupported_database_urls(database_url: str) -> None:
    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        valid_settings(database_url=database_url)


def test_rejects_placeholder_secret_in_production() -> None:
    with pytest.raises(ValidationError, match="non-development JWT secret"):
        valid_settings(
            app_env="production",
            jwt_secret_key="replace-with-at-least-32-random-characters",
        )


@pytest.mark.parametrize(
    "field",
    [
        "login_rate_limit_email_failures",
        "login_rate_limit_ip_failures",
        "login_rate_limit_window_seconds",
    ],
)
def test_rejects_non_positive_login_rate_limit_settings(field: str) -> None:
    with pytest.raises(ValidationError):
        valid_settings(**{field: 0})
