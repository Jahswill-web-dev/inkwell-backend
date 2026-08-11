from __future__ import annotations

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.core.config import Settings
from app.main import create_app


def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def require_test_url(settings: Settings) -> str:
    assert settings.test_database_url is not None
    return settings.test_database_url


@pytest.mark.database
def test_migrations_upgrade_downgrade_and_restore(settings: Settings) -> None:
    database_url = require_test_url(settings)
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    try:
        command.downgrade(config, "20260810_0001")
    finally:
        command.upgrade(config, "head")


@pytest.mark.database
def test_database_readiness(settings: Settings) -> None:
    test_settings = settings.model_copy(update={"database_url": require_test_url(settings)})
    with TestClient(create_app(test_settings)) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
