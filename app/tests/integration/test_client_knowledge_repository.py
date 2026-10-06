from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete

from alembic import command
from app.core.config import Settings
from app.db.models.article import Article
from app.db.models.client import Client
from app.db.models.interview_invitation import InterviewInvitation
from app.db.models.interview_transcript import InterviewTranscript
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.user import User
from app.db.models.workspace import Workspace, WorkspaceMember
from app.db.repositories.client_knowledge import ClientKnowledgeRepository
from app.db.session import create_engine, create_session_factory
from app.schemas.client_knowledge import ClientKnowledgeProposalCreate
from app.tests.integration.test_database import alembic_config, require_test_url

pytestmark = pytest.mark.database


async def exercise_repository(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            await session.execute(delete(LoginRateLimit))
            await session.execute(delete(Workspace))
            await session.execute(delete(User))

            user_id = uuid4()
            workspace_id = uuid4()
            client_id = uuid4()
            article_id = uuid4()
            invitation_id = uuid4()
            transcript_id = uuid4()
            session.add_all(
                [
                    User(
                        id=user_id,
                        email="knowledge-owner@example.com",
                        username="knowledge_owner",
                        password_hash="not-used",
                    ),
                    Workspace(id=workspace_id, name="Knowledge workspace"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    WorkspaceMember(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        role="owner",
                        is_default=True,
                    ),
                    Client(id=client_id, workspace_id=workspace_id, name="Northstar Labs"),
                ]
            )
            await session.flush()
            session.add(
                Article(
                    id=article_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    client_id=client_id,
                    notes="Interview context",
                    working_title="A useful article",
                    target_audience=["Content leaders"],
                    article_goal="inform_and_inspire",
                )
            )
            await session.flush()
            session.add(
                InterviewInvitation(
                    id=invitation_id,
                    article_id=article_id,
                    workspace_id=workspace_id,
                    participant_name="Avery Chen",
                    participant_email="avery@example.com",
                    token="knowledge-test-token",
                )
            )
            await session.flush()
            session.add(
                InterviewTranscript(
                    id=transcript_id,
                    invitation_id=invitation_id,
                    turns=[
                        {
                            "item_id": "turn-1",
                            "speaker": "participant",
                            "text": "Customers reduce onboarding time by 40%.",
                            "occurred_at_ms": 1_200,
                        }
                    ],
                )
            )
            await session.flush()

            repository = ClientKnowledgeRepository(session)
            run, created = await repository.create_run_if_missing(
                workspace_id=workspace_id,
                client_id=client_id,
                transcript_id=transcript_id,
                prompt_version="knowledge-v1",
                transcript_content_hash="a" * 64,
                model_id="test-model",
            )
            same_run, created_again = await repository.create_run_if_missing(
                workspace_id=workspace_id,
                client_id=client_id,
                transcript_id=transcript_id,
                prompt_version="knowledge-v1",
                transcript_content_hash="a" * 64,
                model_id="test-model",
            )

            assert created is True
            assert created_again is False
            assert same_run.id == run.id

            claimed_run = await repository.claim_run_for_processing(run.id)
            duplicate_claim = await repository.claim_run_for_processing(run.id)

            assert claimed_run is not None
            assert claimed_run.id == run.id
            assert claimed_run.status == "processing"
            assert duplicate_claim is None

            proposals = await repository.save_proposals(
                claimed_run,
                article_id=article_id,
                interviewee_name="Avery Chen",
                proposals=[
                    ClientKnowledgeProposalCreate(
                        category="proof_points_and_results",
                        statement="Customers reduce onboarding time by 40%.",
                        knowledge_type="fact",
                        confidence=0.82,
                        needs_verification=True,
                        sources=[
                            {
                                "transcript_item_id": "turn-1",
                                "quoted_text": "Customers reduce onboarding time by 40%.",
                                "occurred_at_ms": 1_200,
                            }
                        ],
                    ),
                    ClientKnowledgeProposalCreate(
                        category="problems_and_needs",
                        statement="Customers want faster onboarding.",
                        knowledge_type="fact",
                        sources=[
                            {
                                "transcript_item_id": "turn-1",
                                "quoted_text": "Customers reduce onboarding time by 40%.",
                                "occurred_at_ms": 1_200,
                            }
                        ],
                    ),
                ],
            )
            repeated = await repository.save_proposals(
                run,
                article_id=article_id,
                interviewee_name="Avery Chen",
                proposals=[],
            )

            assert run.status == "completed"
            assert len(proposals) == 2
            assert {proposal.id for proposal in repeated} == {proposal.id for proposal in proposals}
            first_sources = await repository.list_sources_for_proposal(proposals[0].id)
            second_sources = await repository.list_sources_for_proposal(proposals[1].id)
            assert len(first_sources) == 1
            assert first_sources[0].id == second_sources[0].id
            assert first_sources[0].transcript_item_id == "turn-1"

            proposal_for_review = await repository.get_proposal_for_review(
                proposal_id=proposals[0].id,
                workspace_id=workspace_id,
            )
            assert proposal_for_review is not None
            reviewed = await repository.record_proposal_review(
                proposal_for_review,
                review_status="approved",
                reviewed_statement="Customers can reduce onboarding time by 40%.",
                review_notes="Clarified the scope of the claim.",
                reviewed_by_user_id=user_id,
            )

            assert reviewed.statement == "Customers reduce onboarding time by 40%."
            assert reviewed.reviewed_statement == ("Customers can reduce onboarding time by 40%.")
            assert reviewed.review_status == "approved"
            assert reviewed.reviewed_by_user_id == user_id
            assert reviewed.reviewed_at is not None
    finally:
        await engine.dispose()


def test_repository_persists_an_idempotent_source_backed_proposal_batch(
    settings: Settings,
) -> None:
    database_url = require_test_url(settings)
    command.upgrade(alembic_config(database_url), "head")
    test_settings = settings.model_copy(update={"database_url": database_url})

    asyncio.run(exercise_repository(test_settings))
