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
from app.db.models.article_draft import ArticleDraft
from app.db.models.consultant import (
    ConsultantDraftRevision,
    ConsultantGeneration,
    ConsultantInterview,
)
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.schemas.consultant import GeneratedDraft
from app.services.consultant_service import COVERAGE_GOALS
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
    interview = client.get(f"/api/v1/consultant/interviews/{iid}", headers=owner).json()
    assert [item["text"] for item in interview["questions"]] == COVERAGE_GOALS
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
    profile = c.get("/api/v1/consultant/profile", headers=h)
    assert profile.status_code == 200
    assert set(profile.json()) == {
        "display_name",
        "specialty",
        "audience",
        "onboarding_complete",
    }
    assert c.put("/api/v1/consultant/voice", headers=h, json={"action": "reset"}).status_code == 404
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


def test_manual_edits_require_current_version_and_keep_one_draft(
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
    saved = c.get(path, headers=h).json()
    assert saved["sections"][0]["title"] == "Clear ownership"
    assert saved["revisions"] == []


def test_proposal_approval_rejects_changed_interview_and_draft(
    consultant_client: TestClient,
) -> None:
    client = consultant_client
    headers = register(client)
    article_id, interview_id = setup_article(client, headers)
    article_path = f"/api/v1/consultant/articles/{article_id}"
    sections = editor_sections(
        GeneratedDraft(
            title="Handoffs",
            sections=[
                {
                    "heading": "Opening",
                    "paragraphs": [
                        {
                            "text": "Give the next step one owner.",
                            "source_turn_ids": ["t1"],
                            "direct_quotes": [],
                        }
                    ],
                }
            ],
            gaps=[],
        )
    )
    assert (
        client.put(
            article_path + "/draft", headers=headers, json={"sections": sections}
        ).status_code
        == 200
    )
    version = client.get(article_path, headers=headers).json()["draft_updated_at"]

    async def seed_proposal(key: str, interview_revision: int, base_version: str) -> str:
        engine = create_engine(client.app.state.settings)
        try:
            async with create_session_factory(engine).begin() as db:
                interview = await db.get(ConsultantInterview, UUID(interview_id))
                assert interview is not None
                interview.state = "completed"
                job = ConsultantGeneration(
                    article_id=UUID(article_id),
                    interview_id=UUID(interview_id),
                    request_key=key,
                    state="succeeded",
                    stage="complete",
                    snapshot={
                        "transcript_revisions": {interview_id: interview_revision},
                        "base_draft_updated_at": base_version,
                        "instruction": key,
                    },
                    model_id="test-model",
                    prompt_version="test",
                )
                db.add(job)
                await db.flush()
                revision = ConsultantDraftRevision(
                    article_id=UUID(article_id),
                    generation_id=job.id,
                    kind="proposal",
                    sections=sections,
                    gaps=[],
                )
                db.add(revision)
                await db.flush()
                return str(revision.id)
        finally:
            await engine.dispose()

    first_id = asyncio.run(seed_proposal("first", 0, version))
    correction = client.put(
        f"/api/v1/consultant/interviews/{interview_id}/corrections",
        headers=headers,
        json={"corrections": {}},
    )
    assert correction.status_code == 200
    stale_interview = client.post(
        article_path + f"/revisions/{first_id}/apply",
        headers={**headers, "If-Unmodified-Since": version},
    )
    assert stale_interview.status_code == 409
    assert stale_interview.json()["error"]["code"] == "stale_proposal"

    second_id = asyncio.run(seed_proposal("second", 1, version))

    async def change_draft_without_deleting_proposal() -> None:
        engine = create_engine(client.app.state.settings)
        try:
            async with create_session_factory(engine).begin() as db:
                draft = await db.scalar(
                    select(ArticleDraft).where(ArticleDraft.article_id == UUID(article_id))
                )
                assert draft is not None
                updated = [dict(item) for item in draft.sections]
                updated[0]["title"] = "Changed elsewhere"
                draft.sections = updated
        finally:
            await engine.dispose()

    asyncio.run(change_draft_without_deleting_proposal())
    latest = client.get(article_path, headers=headers).json()["draft_updated_at"]
    stale_draft = client.post(
        article_path + f"/revisions/{second_id}/apply",
        headers={**headers, "If-Unmodified-Since": latest},
    )
    assert stale_draft.status_code == 409
    assert stale_draft.json()["error"]["code"] == "stale_proposal"


def test_worker_claim_recovery_and_proposals_preserve_manual_edits(
    consultant_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.schemas.consultant import (
        ArticleEditPlan,
        ArticleSectionEdits,
        DraftCheck,
    )
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
    queued = client.post(path + "/finish", headers=h).json()
    job_id = queued["id"]
    assert queued["stage"] == "write"

    class FakeWriter:
        def __init__(self, *args: object) -> None:
            self.client = SimpleNamespace(close=AsyncMock())
            self.usage = {"input_tokens": 0, "output_tokens": 0}

        async def write(self, snapshot: dict[str, Any]) -> GeneratedDraft:
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
            return DraftCheck(findings=[])

        async def plan_article_edit(self, source: dict[str, Any]) -> ArticleEditPlan:
            return ArticleEditPlan(
                actions=[
                    {
                        "kind": "text_edit",
                        "target_section_id": source["outline"][0]["id"],
                        "instruction": "Make the article more structured",
                    }
                ]
            )

        async def edit_article_sections(self, source: dict[str, Any]) -> ArticleSectionEdits:
            return ArticleSectionEdits(
                repairs=[
                    {
                        "section_index": source["targets"][0]["section_index"],
                        "section": {
                            "heading": "Ownership",
                            "paragraphs": [
                                {
                                    "text": "I call this expensive confusion.",
                                    "source_turn_ids": [source["participant_turns"][0]["item_id"]],
                                    "direct_quotes": [],
                                }
                            ],
                        },
                    }
                ],
                summary="Improved structure.",
            )

        async def check_article_changes(self, source: dict[str, Any]) -> DraftCheck:
            return DraftCheck(findings=[])

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
    assert current["revisions"] == []
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
    current = client.get(article_path, headers=h).json()
    ai_path = article_path + "/ai-requests"
    payload = {"request_key": "rewrite", "instruction": "Make the article more structured"}
    assert client.post(ai_path, headers=h, json=payload).status_code == 409
    requested = client.post(
        ai_path,
        headers={**h, "If-Unmodified-Since": current["draft_updated_at"]},
        json=payload,
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["stage"] == "plan"

    async def inspect_direct_draft_job() -> None:
        engine = create_engine(client.app.state.settings)
        try:
            async with create_session_factory(engine)() as db:
                rewrite_job = await db.get(ConsultantGeneration, UUID(requested.json()["id"]))
                assert rewrite_job is not None
                assert rewrite_job.evidence is None
                assert rewrite_job.stage == "plan"
        finally:
            await engine.dispose()

    asyncio.run(inspect_direct_draft_job())
    assert (
        client.post(
            ai_path,
            headers={**h, "If-Unmodified-Since": current["draft_updated_at"]},
            json=payload,
        ).json()["id"]
        == requested.json()["id"]
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
    assert len(result["revisions"]) == 1
    assert result["revisions"][0]["kind"] == "proposal"
    assert result["revisions"][0]["request"] == "Make the article more structured"
    assert result["revisions"][0]["sources"] == []
    assert (
        client.delete(
            article_path + f"/revisions/{result['revisions'][0]['id']}", headers=h
        ).status_code
        == 200
    )
    assert client.get(article_path, headers=h).json()["revisions"] == []
    second = client.post(
        ai_path,
        headers={**h, "If-Unmodified-Since": current["draft_updated_at"]},
        json={**payload, "request_key": "rewrite-again"},
    )
    assert second.status_code == 200, second.text
    asyncio.run(rewrite())
    result = client.get(article_path, headers=h).json()
    assert (
        client.post(
            article_path + f"/revisions/{result['revisions'][0]['id']}/apply",
            headers={**h, "If-Unmodified-Since": result["draft_updated_at"]},
        ).status_code
        == 200
    )
    applied = client.get(article_path, headers=h).json()
    assert applied["sections"][0]["title"] == "Ownership"
    assert applied["revisions"] == []


def test_worker_saves_checker_objection_with_reviewable_article(
    consultant_client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.schemas.consultant import DraftCheck, SectionRepairs
    from app.workers import consultant as worker

    client = consultant_client
    headers = register(client)
    article_id, interview_id = setup_article(client, headers)
    interview_path = f"/api/v1/consultant/interviews/{interview_id}"
    client.post(
        interview_path + "/transcript",
        headers=headers,
        json={
            "turns": [
                {
                    "item_id": "one",
                    "speaker": "participant",
                    "text": "Fix the handoff before you buy another tool.",
                }
            ]
        },
    )
    job_id = client.post(interview_path + "/finish", headers=headers).json()["id"]
    objection = "The example is attributed to the consultant without interview evidence."

    class FakeWriter:
        def __init__(self, *args: object) -> None:
            self.client = SimpleNamespace(close=AsyncMock())
            self.usage = {"input_tokens": 0, "output_tokens": 0}

        async def write(self, snapshot: dict[str, Any]) -> GeneratedDraft:
            return GeneratedDraft(
                title="Handoffs",
                sections=[
                    {
                        "heading": "Ownership",
                        "paragraphs": [
                            {
                                "text": "Fix the handoff before you buy another tool.",
                                "source_turn_ids": [snapshot["turns"][0]["item_id"]],
                                "direct_quotes": [],
                            }
                        ],
                    }
                ],
                gaps=[],
            )

        async def check(self, snapshot: dict[str, Any], draft: GeneratedDraft) -> DraftCheck:
            return DraftCheck(
                findings=[
                    {
                        "severity": "serious",
                        "category": "invented_experience",
                        "section_index": 0,
                        "paragraph_index": 0,
                        "explanation": objection,
                        "source_turn_ids": [snapshot["turns"][0]["item_id"]],
                    }
                ]
            )

        async def repair_sections(
            self, snapshot: dict[str, Any], draft: GeneratedDraft, findings: list[Any]
        ) -> SectionRepairs:
            return SectionRepairs(
                repairs=[
                    {
                        "section_index": 0,
                        "section": draft.sections[0].model_dump(),
                    }
                ]
            )

        async def check_repaired_sections(
            self, snapshot: dict[str, Any], draft: GeneratedDraft, indices: set[int]
        ) -> DraftCheck:
            return await self.check(snapshot, draft)

        async def parse(
            self, schema: type[GeneratedDraft], task: str, source: dict[str, Any]
        ) -> GeneratedDraft:
            return GeneratedDraft.model_validate(source["candidate"])

    monkeypatch.setattr(worker, "ConsultantWriter", FakeWriter)
    monkeypatch.setattr(worker, "get_settings", lambda: client.app.state.settings)

    async def run_worker() -> None:
        engine = create_engine(client.app.state.settings)
        factory = create_session_factory(engine)
        try:
            claimed = await worker.claim(factory)
            assert claimed is not None
            await worker.process(factory, *claimed)
        finally:
            await engine.dispose()

    asyncio.run(run_worker())
    article = client.get(f"/api/v1/consultant/articles/{article_id}", headers=headers).json()
    assert article["job"]["id"] == job_id
    assert article["job"]["state"] == "succeeded"
    assert article["job"]["error"] is None
    assert article["job"]["open_finding_count"] == 1
    assert article["open_finding_count"] == 1
    assert article["findings"][0]["explanation"] == objection
    assert objection not in caplog.text


def test_initial_style_suggestion_saves_draft_without_repair(
    consultant_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.schemas.consultant import DraftCheck
    from app.workers import consultant as worker

    client = consultant_client
    headers = register(client)
    article_id, interview_id = setup_article(client, headers)
    interview_path = f"/api/v1/consultant/interviews/{interview_id}"
    client.post(
        interview_path + "/transcript",
        headers=headers,
        json={
            "turns": [
                {
                    "item_id": "one",
                    "speaker": "participant",
                    "text": "Fix the handoff before you buy another tool.",
                }
            ]
        },
    )
    client.post(interview_path + "/finish", headers=headers)

    class FakeWriter:
        def __init__(self, *args: object) -> None:
            self.client = SimpleNamespace(close=AsyncMock())
            self.usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}

        async def write(self, snapshot: dict[str, Any]) -> GeneratedDraft:
            return GeneratedDraft(
                title="Handoffs",
                sections=[
                    {
                        "heading": "Opening",
                        "paragraphs": [
                            {
                                "text": "Fix the handoff before you buy another tool.",
                                "source_turn_ids": [snapshot["turns"][0]["item_id"]],
                                "direct_quotes": [],
                            }
                        ],
                    }
                ],
                gaps=[],
            )

        async def check(self, snapshot: dict[str, Any], draft: GeneratedDraft) -> DraftCheck:
            return DraftCheck(
                findings=[
                    {
                        "severity": "style",
                        "category": "wording",
                        "section_index": 0,
                        "paragraph_index": 0,
                        "explanation": "Consider a shorter first sentence.",
                    }
                ]
            )

    monkeypatch.setattr(worker, "ConsultantWriter", FakeWriter)
    monkeypatch.setattr(worker, "get_settings", lambda: client.app.state.settings)

    async def run_worker() -> None:
        engine = create_engine(client.app.state.settings)
        factory = create_session_factory(engine)
        try:
            claimed = await worker.claim(factory)
            assert claimed is not None
            await worker.process(factory, *claimed)
        finally:
            await engine.dispose()

    asyncio.run(run_worker())
    article = client.get(f"/api/v1/consultant/articles/{article_id}", headers=headers).json()
    assert article["job"]["state"] == "succeeded"
    assert article["sections"][0]["title"] == "Opening"
    assert article["job"]["writing_suggestions"] == [
        {
            "section": "Opening",
            "text": "Consider a shorter first sentence.",
        }
    ]

    async def inspect_metrics() -> None:
        engine = create_engine(client.app.state.settings)
        try:
            async with create_session_factory(engine)() as db:
                job = await db.scalar(
                    select(ConsultantGeneration).where(
                        ConsultantGeneration.article_id == UUID(article_id)
                    )
                )
                assert job is not None
                assert set(job.usage["stage_seconds"]) >= {
                    "write",
                    "check_full",
                    "total",
                }
                assert "understand" not in job.usage["stage_seconds"]
                assert "repair_sections" not in job.usage["stage_seconds"]
        finally:
            await engine.dispose()

    asyncio.run(inspect_metrics())
