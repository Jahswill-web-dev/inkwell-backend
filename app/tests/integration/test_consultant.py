from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from app.core.config import Settings
from app.db.models.consultant import ConsultantGeneration
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.schemas.consultant import GeneratedDraft
from app.services.consultant_writer import editor_sections
from app.tests.integration.test_database import alembic_config, require_test_url
from app.tests.integration.test_interview_invitations import clear_database

pytestmark = pytest.mark.database


@pytest.fixture
def consultant_client(settings: Settings) -> Iterator[TestClient]:
    url = require_test_url(settings)
    command.upgrade(alembic_config(url), "head")
    config = settings.model_copy(update={"database_url": url, "consultant_enabled": True})
    asyncio.run(clear_database(config))
    with TestClient(create_app(config)) as client:
        yield client


def register(
    client: TestClient, name: str = "consultant", experience: str = "consultant"
) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{name}@example.com",
            "username": name,
            "password": "correct horse battery staple",
            "experience": experience,
        },
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def setup_article(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    a = client.post(
        "/api/v1/consultant/articles",
        headers=headers,
        json={"title": "Better handoffs", "description": "Why clear ownership matters"},
    )
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    i = client.post(f"/api/v1/consultant/articles/{aid}/interviews", headers=headers)
    assert i.status_code == 200, i.text
    return aid, i.json()["id"]


def test_signup_isolation_finalization_and_snapshots(consultant_client: TestClient) -> None:
    client = consultant_client
    owner = register(client)
    stranger = register(client, "stranger", "marketing")
    aid, iid = setup_article(client, owner)
    assert client.get(f"/api/v1/consultant/interviews/{iid}", headers=stranger).status_code == 404
    assert client.get(f"/api/v1/articles/{aid}", headers=stranger).status_code == 404
    assert client.get("/api/v1/articles", headers=owner).json()["items"] == []
    path = f"/api/v1/consultant/interviews/{iid}"
    assert client.post(path + "/finish", headers=owner).status_code == 422
    turn = {
        "item_id": "one",
        "speaker": "participant",
        "text": "I call this expensive confusion. Fix the handoff before you buy another tool.",
    }
    for _ in range(2):
        assert (
            client.post(path + "/transcript", headers=owner, json={"turns": [turn]}).status_code
            == 200
        )
    assert len(client.get(path, headers=owner).json()["turns"]) == 1
    first = client.post(path + "/finish", headers=owner)
    again = client.post(path + "/finish", headers=owner)
    assert first.status_code == 200, first.text
    assert again.json()["id"] == first.json()["id"]
    assert (
        client.post(
            path + "/transcript", headers=owner, json={"turns": [{**turn, "item_id": "late"}]}
        ).status_code
        == 409
    )
    assert (
        client.put(
            path + "/corrections",
            headers=owner,
            json={"corrections": {"one": {"text": "Corrected wording", "excluded": False}}},
        ).status_code
        == 200
    )

    async def inspect() -> None:
        engine = create_engine(client.app.state.settings)
        try:
            async with create_session_factory(engine)() as db:
                jobs = (await db.scalars(select(ConsultantGeneration))).all()
                assert len(jobs) == 1
                assert jobs[0].snapshot["turns"][0]["text"] == turn["text"]
        finally:
            await engine.dispose()

    asyncio.run(inspect())


def test_switch_is_idempotent_and_preserves_existing_article(consultant_client: TestClient) -> None:
    c = consultant_client
    h = register(c)
    aid, _ = setup_article(c, h)
    first = c.get("/api/v1/workspaces/current", headers=h).json()["id"]
    for experience in ["marketing", "consultant", "consultant"]:
        assert (
            c.put(
                "/api/v1/consultant/experience", headers=h, json={"experience": experience}
            ).status_code
            == 200
        )
    assert c.get("/api/v1/workspaces/current", headers=h).json()["id"] == first
    assert c.get(f"/api/v1/consultant/articles/{aid}", headers=h).status_code == 200


def test_manual_edits_require_current_version_and_save_history(
    consultant_client: TestClient,
) -> None:
    c = consultant_client
    h = register(c)
    aid, _ = setup_article(c, h)
    sections = editor_sections(
        GeneratedDraft(
            title="Handoffs",
            sections=[
                {
                    "heading": "Ownership",
                    "paragraphs": [
                        {
                            "text": "Own the next step.",
                            "source_turn_ids": ["one"],
                            "direct_quotes": [],
                        }
                    ],
                }
            ],
            gaps=[],
        )
    )
    path = f"/api/v1/consultant/articles/{aid}"
    assert c.put(path + "/draft", headers=h, json={"sections": sections}).status_code == 200
    current = c.get(path, headers=h).json()
    assert c.put(path + "/draft", headers=h, json={"sections": sections}).status_code == 409
    sections[0]["title"] = "Clear ownership"
    assert (
        c.put(
            path + "/draft",
            headers={**h, "If-Unmodified-Since": current["draft_updated_at"]},
            json={"sections": sections},
        ).status_code
        == 200
    )
    assert len(c.get(path, headers=h).json()["revisions"]) >= 3


def test_worker_claim_recovery_and_proposals_preserve_manual_edits(
    consultant_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.schemas.consultant import DraftCheck, Evidence
    from app.workers import consultant as worker

    client = consultant_client
    h = register(client)
    aid, iid = setup_article(client, h)
    path = f"/api/v1/consultant/interviews/{iid}"
    client.post(
        path + "/transcript",
        headers=h,
        json={
            "turns": [
                {
                    "item_id": "one",
                    "speaker": "participant",
                    "text": "I call this expensive confusion. Fix the handoff first.",
                }
            ]
        },
    )
    job_id = client.post(path + "/finish", headers=h).json()["id"]

    class FakeWriter:
        def __init__(self, *args: object) -> None:
            self.client = SimpleNamespace(close=AsyncMock())
            self.usage = {"input_tokens": 0, "output_tokens": 0}

        async def understand(self, snapshot: dict[str, Any]) -> Evidence:
            return Evidence(
                argument="Fix handoffs",
                claims=[],
                outline=["Ownership"],
                gaps=[],
                voice={"excerpts": ["expensive confusion"]},
            )

        async def write(self, snapshot: dict[str, Any], evidence: dict[str, Any]) -> GeneratedDraft:
            return GeneratedDraft(
                title="Handoffs",
                sections=[
                    {
                        "heading": "Ownership",
                        "paragraphs": [
                            {
                                "text": "I call this expensive confusion.",
                                "source_turn_ids": [snapshot["turns"][0]["item_id"]],
                                "direct_quotes": [],
                            }
                        ],
                    }
                ],
                gaps=[],
            )

        async def check(self, snapshot: dict[str, Any], candidate: GeneratedDraft) -> DraftCheck:
            return DraftCheck(serious_issues=[], style_issues=[])

    monkeypatch.setattr(worker, "ConsultantWriter", FakeWriter)
    monkeypatch.setattr(worker, "get_settings", lambda: client.app.state.settings)

    async def process_and_recover() -> None:
        engine = create_engine(client.app.state.settings)
        factory = create_session_factory(engine)
        try:
            first = await worker.claim(factory)
            assert first is not None
            assert await worker.claim(factory) is None
            async with factory.begin() as db:
                record = await db.get(ConsultantGeneration, UUID(job_id))
                assert record is not None
                record.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            recovered = await worker.claim(factory)
            assert recovered is not None and recovered[0] == first[0]
            assert recovered[1] != first[1]
            await worker.process(factory, *recovered)
            async with factory() as db:
                record = await db.get(ConsultantGeneration, UUID(job_id))
                assert record is not None
                assert record.state == "succeeded"
                assert record.attempts == 2
        finally:
            await engine.dispose()

    asyncio.run(process_and_recover())
    article_path = f"/api/v1/consultant/articles/{aid}"
    current = client.get(article_path, headers=h).json()
    edited = current["sections"]
    edited[0]["title"] = "My manual edit"
    assert (
        client.put(
            article_path + "/draft",
            headers={**h, "If-Unmodified-Since": current["draft_updated_at"]},
            json={"sections": edited},
        ).status_code
        == 200
    )
    assert (
        client.post(
            path + "/generate", headers=h, json={"request_key": "rewrite", "instruction": "closer"}
        ).status_code
        == 200
    )

    async def rewrite() -> None:
        engine = create_engine(client.app.state.settings)
        factory = create_session_factory(engine)
        try:
            claimed = await worker.claim(factory)
            assert claimed is not None
            await worker.process(factory, *claimed)
        finally:
            await engine.dispose()

    asyncio.run(rewrite())
    result = client.get(article_path, headers=h).json()
    assert result["sections"][0]["title"] == "My manual edit"
    assert result["revisions"][0]["kind"] == "proposal"
