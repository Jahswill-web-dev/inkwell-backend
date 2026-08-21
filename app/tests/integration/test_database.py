from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app


def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def require_test_url(settings: Settings) -> str:
    assert settings.test_database_url is not None
    return settings.test_database_url


async def insert_legacy_article(settings: Settings, *, user_id: UUID, article_id: UUID) -> None:
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, email, username, password_hash) "
                    "VALUES (:id, :email, :username, :password_hash)"
                ),
                {
                    "id": user_id,
                    "email": f"migration-{user_id}@example.com",
                    "username": f"migration_{str(user_id).replace('-', '')[:12]}",
                    "password_hash": "not-used",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO articles "
                    "(id, user_id, notes, working_title, target_audience, article_goal) "
                    "VALUES (:id, :user_id, :notes, :working_title, "
                    ":target_audience, :article_goal)"
                ),
                {
                    "id": article_id,
                    "user_id": user_id,
                    "notes": "Migration notes",
                    "working_title": "Migration title",
                    "target_audience": "Legacy audience",
                    "article_goal": "inform_and_inspire",
                },
            )
    finally:
        await engine.dispose()


async def read_target_audience(settings: Settings, *, article_id: UUID) -> object:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(
                text("SELECT target_audience FROM articles WHERE id = :id"),
                {"id": article_id},
            )
    finally:
        await engine.dispose()


async def insert_outline_without_section_ids(
    settings: Settings, *, user_id: UUID, article_id: UUID, outline_id: UUID
) -> None:
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, email, username, password_hash) "
                    "VALUES (:id, :email, :username, :password_hash)"
                ),
                {
                    "id": user_id,
                    "email": f"outline-migration-{user_id}@example.com",
                    "username": f"outline_{str(user_id).replace('-', '')[:12]}",
                    "password_hash": "not-used",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO articles "
                    "(id, user_id, notes, working_title, target_audience, article_goal) "
                    "VALUES (:id, :user_id, 'Notes', 'Title', ARRAY['Audience'], "
                    "'inform_and_inspire')"
                ),
                {"id": article_id, "user_id": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO article_outlines "
                    "(id, article_id, sections, source_brief_revision, model_id, "
                    "prompt_version, generation_duration_ms) "
                    "VALUES (:id, :article_id, CAST(:sections AS jsonb), 1, 'model', "
                    "'version', 1)"
                ),
                {
                    "id": outline_id,
                    "article_id": article_id,
                    "sections": json.dumps(
                        [
                            {"heading": "One", "purpose": "Goal", "key_points": ["Point"]},
                            {"heading": "Two", "purpose": "Goal", "key_points": ["Point"]},
                            {"heading": "Three", "purpose": "Goal", "key_points": ["Point"]},
                        ]
                    ),
                },
            )
    finally:
        await engine.dispose()


async def read_outline_sections(settings: Settings, *, outline_id: UUID) -> list[object]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            sections = await connection.scalar(
                text("SELECT sections FROM article_outlines WHERE id = :id"),
                {"id": outline_id},
            )
            assert isinstance(sections, list)
            return sections
    finally:
        await engine.dispose()


@pytest.mark.database
def test_migrations_upgrade_downgrade_and_restore(settings: Settings) -> None:
    database_url = require_test_url(settings)
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260812_0004")
    test_settings = settings.model_copy(update={"database_url": database_url})
    user_id = uuid4()
    article_id = uuid4()
    asyncio.run(insert_legacy_article(test_settings, user_id=user_id, article_id=article_id))
    try:
        command.upgrade(config, "head")
        assert asyncio.run(read_target_audience(test_settings, article_id=article_id)) == [
            "Legacy audience"
        ]
        command.downgrade(config, "20260812_0004")
        assert (
            asyncio.run(read_target_audience(test_settings, article_id=article_id))
            == "Legacy audience"
        )
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


@pytest.mark.database
def test_draft_migration_backfills_outline_section_ids(settings: Settings) -> None:
    database_url = require_test_url(settings)
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260818_0007")
    test_settings = settings.model_copy(update={"database_url": database_url})
    user_id, article_id, outline_id = uuid4(), uuid4(), uuid4()
    asyncio.run(
        insert_outline_without_section_ids(
            test_settings, user_id=user_id, article_id=article_id, outline_id=outline_id
        )
    )
    try:
        command.upgrade(config, "head")
        sections = asyncio.run(read_outline_sections(test_settings, outline_id=outline_id))
        section_ids = [
            UUID(str(section["id"])) for section in sections if isinstance(section, dict)
        ]
        assert len(section_ids) == 3
        assert len(set(section_ids)) == 3
    finally:
        command.upgrade(config, "head")
