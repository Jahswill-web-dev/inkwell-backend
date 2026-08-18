from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Inkwell API"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    api_v1_prefix: str = "/api/v1"

    database_url: str
    test_database_url: str | None = None
    database_echo: bool = False

    cors_origins: list[str] = Field(default_factory=list)

    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, gt=0)
    jwt_issuer: str = "inkwell-api"
    jwt_audience: str = "inkwell-client"

    login_rate_limit_email_failures: int = Field(default=5, gt=0)
    login_rate_limit_ip_failures: int = Field(default=20, gt=0)
    login_rate_limit_window_seconds: int = Field(default=900, gt=0)

    vertex_project_id: str | None = None
    vertex_location: str = "global"
    vertex_model_id: str = "gemini-2.5-flash"
    vertex_request_timeout_seconds: float = Field(default=45, gt=0)
    vertex_max_output_tokens: int = Field(default=4096, gt=0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        normalized = "/" + value.strip("/")
        if normalized == "/":
            raise ValueError("API_V1_PREFIX must not be the root path")
        return normalized

    @field_validator("database_url", "test_database_url")
    @classmethod
    def validate_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            url = make_url(value)
        except Exception as exc:
            raise ValueError(
                "Database URL must be valid and use the postgresql+psycopg async driver"
            ) from exc
        if url.drivername != "postgresql+psycopg":
            raise ValueError("Database URL must use the postgresql+psycopg async driver")
        return value

    @model_validator(mode="after")
    def reject_development_secret_in_production(self) -> Self:
        if self.app_env == "production" and self.jwt_secret_key.startswith("replace-"):
            raise ValueError("A non-development JWT secret is required in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""

    return Settings()
