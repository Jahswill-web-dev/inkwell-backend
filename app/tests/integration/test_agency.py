from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from alembic import command
from app.core.config import Settings
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.user import User
from app.db.models.workspace import Workspace
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.tests.integration.test_database import alembic_config, require_test_url

pytestmark = pytest.mark.database


async def clear_database(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            await session.execute(delete(LoginRateLimit))
            await session.execute(delete(Workspace))
            await session.execute(delete(User))
    finally:
        await engine.dispose()


@pytest.fixture
def agency_context(settings: Settings) -> Iterator[TestClient]:
    database_url = require_test_url(settings)
    command.upgrade(alembic_config(database_url), "head")
    test_settings = settings.model_copy(update={"database_url": database_url})
    asyncio.run(clear_database(test_settings))
    with TestClient(create_app(test_settings)) as client:
        yield client


def register(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{username}@example.com",
            "username": username,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    return str(response.json()["access_token"])


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def article_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "notes": "Structured setup context",
        "working_title": "A useful article",
        "target_audience": ["Content leaders"],
        "article_goal": "inform_and_inspire",
        "content_type": "thought_leadership",
        "due_date": "2026-10-01",
        "target_length": "long",
        "interview_method": "client",
        "interviewee_name": "Avery Chen",
        "interview_instructions": "Ask for measurable examples",
        "main_angle": "Expert evidence builds trust",
        "key_message": "Interview before drafting",
        "call_to_action": "Book a strategy call",
        "tone": "Clear and credible",
        "seo_keyword": "expert-led content",
    }
    payload.update(overrides)
    return payload


def test_registration_creates_default_workspace(agency_context: TestClient) -> None:
    token = register(agency_context, "workspace_owner")

    response = agency_context.get("/api/v1/workspaces/current", headers=headers(token))

    assert response.status_code == 200
    assert response.json()["name"] == "workspace_owner's workspace"
    assert response.json()["role"] == "owner"


def test_clients_are_reusable_unique_and_workspace_isolated(
    agency_context: TestClient,
) -> None:
    owner_token = register(agency_context, "client_owner")
    other_token = register(agency_context, "client_other")
    payload = {
        "name": "Northstar Labs",
        "website": "https://northstar.example",
        "industry": "Software",
        "brand_profile": {
            "default_audience": "B2B content leaders",
            "brand_voice": "Direct and practical",
            "preferred_terminology": ["customers"],
            "avoided_terminology": ["users"],
            "default_calls_to_action": ["Book a strategy call"],
        },
    }

    created = agency_context.post("/api/v1/clients", json=payload, headers=headers(owner_token))
    assert created.status_code == 201
    client_id = created.json()["id"]
    assert created.json()["brand_profile"]["brand_voice"] == "Direct and practical"
    assert (
        agency_context.post(
            "/api/v1/clients",
            json={"name": "  NORTHSTAR LABS  "},
            headers=headers(owner_token),
        ).status_code
        == 409
    )
    assert (
        agency_context.post(
            "/api/v1/clients",
            json={"name": "Northstar Labs"},
            headers=headers(other_token),
        ).status_code
        == 201
    )
    hidden = agency_context.get(f"/api/v1/clients/{client_id}", headers=headers(other_token))
    assert hidden.status_code == 404
    updated = agency_context.patch(
        f"/api/v1/clients/{client_id}",
        json={"industry": "B2B software"},
        headers=headers(owner_token),
    )
    assert updated.status_code == 200
    assert updated.json()["industry"] == "B2B software"
    assert agency_context.get("/api/v1/clients", headers=headers(owner_token)).json()["total"] == 1


def test_article_workflow_metadata_is_persisted_and_isolated(
    agency_context: TestClient,
) -> None:
    owner_token = register(agency_context, "article_owner")
    other_token = register(agency_context, "article_other")
    client = agency_context.post(
        "/api/v1/clients",
        json={"name": "Northstar Labs"},
        headers=headers(owner_token),
    ).json()

    created = agency_context.post(
        "/api/v1/articles",
        json=article_payload(client_id=client["id"]),
        headers=headers(owner_token),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["client"] == {"id": client["id"], "name": "Northstar Labs"}
    assert body["assignee"]["username"] == "article_owner"
    assert body["status"] == "setup"
    assert body["due_date"] == "2026-10-01"
    assert body["main_angle"] == "Expert evidence builds trust"
    assert body["workspace_id"]
    assert (
        agency_context.get(
            f"/api/v1/articles/{body['id']}", headers=headers(other_token)
        ).status_code
        == 404
    )


def test_article_defaults_and_workspace_references_are_validated(
    agency_context: TestClient,
) -> None:
    owner_token = register(agency_context, "legacy_owner")
    other_token = register(agency_context, "legacy_other")
    foreign_client = agency_context.post(
        "/api/v1/clients",
        json={"name": "Foreign client"},
        headers=headers(other_token),
    ).json()
    other_user = agency_context.get("/api/v1/auth/me", headers=headers(other_token)).json()

    legacy = agency_context.post(
        "/api/v1/articles",
        json={
            "notes": "Legacy notes",
            "working_title": "Legacy article",
            "target_audience": ["Readers"],
            "article_goal": "inform_and_inspire",
        },
        headers=headers(owner_token),
    )
    assert legacy.status_code == 201
    assert legacy.json()["client"] is None
    assert legacy.json()["content_type"] == "blog_post"
    assert legacy.json()["interview_method"] == "notes"

    invalid_client = agency_context.post(
        "/api/v1/articles",
        json=article_payload(client_id=foreign_client["id"]),
        headers=headers(owner_token),
    )
    assert invalid_client.status_code == 422
    assert invalid_client.json()["error"]["code"] == "invalid_article_client"
    invalid_assignee = agency_context.post(
        "/api/v1/articles",
        json=article_payload(assignee_id=other_user["id"]),
        headers=headers(owner_token),
    )
    assert invalid_assignee.status_code == 422
    assert invalid_assignee.json()["error"]["code"] == "invalid_article_assignee"


def test_published_status_requires_a_matching_timestamp(
    agency_context: TestClient,
) -> None:
    token = register(agency_context, "publisher")
    article = agency_context.post(
        "/api/v1/articles",
        json=article_payload(),
        headers=headers(token),
    ).json()

    invalid = agency_context.patch(
        f"/api/v1/articles/{article['id']}",
        json={"status": "published"},
        headers=headers(token),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_article_workflow"
    published = agency_context.patch(
        f"/api/v1/articles/{article['id']}",
        json={"status": "published", "published_at": "2026-09-10T12:00:00Z"},
        headers=headers(token),
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
