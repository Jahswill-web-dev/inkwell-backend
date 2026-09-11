from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from alembic import command
from app.core.config import Settings
from app.db.models.interview_invitation import InterviewInvitation
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
            await session.execute(delete(InterviewInvitation))
            await session.execute(delete(LoginRateLimit))
            await session.execute(delete(Workspace))
            await session.execute(delete(User))
    finally:
        await engine.dispose()


@pytest.fixture
def client_context(settings: Settings) -> Iterator[TestClient]:
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


def create_article(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/articles",
        json={
            "notes": "Testing guest interviews",
            "working_title": "How to Conduct Interviews",
            "target_audience": ["Content teams"],
            "article_goal": "educate_with_practical_guidance",
            "interview_method": "client",
            "interviewee_name": "Sarah Miller",
        },
        headers=headers(token),
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_create_and_access_guest_interview(client_context: TestClient) -> None:
    writer_token = register(client_context, "writer_inviter")
    article_id = create_article(client_context, writer_token)

    # 1. Writer creates invitation
    create_res = client_context.post(
        f"/api/v1/articles/{article_id}/invitations",
        json={
            "participant_name": "Sarah Miller",
            "participant_email": "sarah@example.com",
            "expires_on": "2030-12-31",
        },
        headers=headers(writer_token),
    )
    assert create_res.status_code == 201
    invitation = create_res.json()
    token = invitation["token"]
    assert invitation["participant_name"] == "Sarah Miller"
    assert invitation["status"] == "active"
    assert invitation["progress_state"] == "not_opened"

    # Verify article status updated to waiting_for_client
    article_res = client_context.get(
        f"/api/v1/articles/{article_id}", headers=headers(writer_token)
    )
    assert article_res.json()["status"] == "waiting_for_client"

    # 2. Guest opens link (public, no auth header)
    guest_res = client_context.get(f"/api/v1/interviews/{token}")
    assert guest_res.status_code == 200
    guest_data = guest_res.json()
    assert guest_data["invitation"]["progress_state"] == "opened"
    assert guest_data["invitation"]["article_title"] == "How to Conduct Interviews"
    assert len(guest_data["session"]["questions"]) >= 5

    # 3. Guest submits an answer
    patch_res = client_context.patch(
        f"/api/v1/interviews/{token}",
        json={
            "state": "active",
            "answers": [
                {
                    "question_id": "key-message",
                    "question": "What is the key message?",
                    "answer": "Ask specific follow-up questions to uncover concrete metrics.",
                    "answered_at": "2026-09-10T12:00:00Z",
                }
            ],
            "current_question_index": 1,
        },
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["invitation"]["progress_state"] == "in_progress"
    assert patch_res.json()["invitation"]["questions_answered"] == 1

    # Check article status is now interview_in_progress
    article_res = client_context.get(
        f"/api/v1/articles/{article_id}", headers=headers(writer_token)
    )
    assert article_res.json()["status"] == "interview_in_progress"

    # 4. Guest completes interview
    complete_res = client_context.patch(
        f"/api/v1/interviews/{token}",
        json={
            "state": "completed",
            "completion_reason": "sufficient",
        },
    )
    assert complete_res.status_code == 200
    assert complete_res.json()["invitation"]["progress_state"] == "completed"

    # Article status transitions to ready_to_draft
    article_res = client_context.get(
        f"/api/v1/articles/{article_id}", headers=headers(writer_token)
    )
    assert article_res.json()["status"] == "ready_to_draft"

    # 5. Writer revokes invitation
    inv_id = invitation["id"]
    revoke_res = client_context.delete(
        f"/api/v1/articles/{article_id}/invitations/{inv_id}",
        headers=headers(writer_token),
    )
    assert revoke_res.status_code == 200
    assert revoke_res.json()["status"] == "revoked"

    # Guest link now returns 403
    forbidden_res = client_context.get(f"/api/v1/interviews/{token}")
    assert forbidden_res.status_code == 403
