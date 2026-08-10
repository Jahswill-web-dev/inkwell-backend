from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://inkwell_test:inkwell_test@localhost:5433/inkwell_test"
)
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://inkwell_test:inkwell_test@localhost:5433/inkwell_test",
)
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
os.environ.setdefault("APP_ENV", "test")

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
