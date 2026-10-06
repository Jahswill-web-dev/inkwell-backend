from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import AppError
from app.schemas.client_knowledge import GeneratedKnowledgeProposals
from app.schemas.interview_transcript import TranscriptTurn
from app.services.client_knowledge_service import (
    ClientKnowledgeGenerationResult,
    ClientKnowledgeService,
    _InterviewContext,
    _transcript_content_hash,
)


def _context(*, progress_state: str = "completed") -> _InterviewContext:
    return _InterviewContext(
        workspace_id=uuid4(),
        client_id=uuid4(),
        transcript_id=uuid4(),
        article_id=uuid4(),
        client_name="Acme",
        client_website="https://example.com",
        client_industry="Software",
        article_title="A useful article",
        interviewee_name="Ada",
        progress_state=progress_state,
        turns=[
            TranscriptTurn(
                item_id="question-1",
                speaker="interviewer",
                text="What makes the product useful?",
            ),
            TranscriptTurn(
                item_id="answer-1",
                speaker="participant",
                text="It cuts reporting time in half.",
                occurred_at_ms=1_200,
            ),
        ],
    )


def _generated(*, source_item_id: str = "answer-1") -> GeneratedKnowledgeProposals:
    return GeneratedKnowledgeProposals.model_validate(
        {
            "proposals": [
                {
                    "category": "proof_points_and_results",
                    "statement": "The product cuts reporting time in half.",
                    "knowledge_type": "fact",
                    "source_item_ids": [source_item_id],
                    "confidence": 0.9,
                    "needs_verification": True,
                }
            ]
        }
    )


def _service(
    context: _InterviewContext,
) -> tuple[ClientKnowledgeService, MagicMock, MagicMock]:
    generator = MagicMock()
    generator.generate = AsyncMock(
        return_value=ClientKnowledgeGenerationResult(proposals=_generated(), model_id="test-model")
    )
    service = ClientKnowledgeService(MagicMock(), generator=generator)
    service._load_interview = AsyncMock(return_value=context)  # type: ignore[method-assign]
    repository = MagicMock()
    service.knowledge = cast(Any, repository)
    return service, generator, repository


async def test_extracts_and_resolves_authoritative_transcript_sources() -> None:
    context = _context()
    service, generator, repository = _service(context)
    run = SimpleNamespace(id=uuid4(), status="pending", model_id=None)
    claimed = SimpleNamespace(id=run.id, status="processing", model_id=None)
    saved = [SimpleNamespace(id=uuid4())]
    repository.create_run_if_missing = AsyncMock(return_value=(run, True))
    repository.claim_run_for_processing = AsyncMock(return_value=claimed)
    repository.save_proposals = AsyncMock(return_value=saved)

    outcome = await service.extract_from_interview(
        client_id=context.client_id,
        interview_id=uuid4(),
    )

    assert outcome.run.id == claimed.id
    assert [proposal.id for proposal in outcome.proposals] == [saved[0].id]
    assert outcome.reused is False
    assert claimed.model_id == "test-model"
    generated_input = generator.generate.await_args.args[0]
    assert generated_input.client_name == "Acme"
    draft = repository.save_proposals.await_args.kwargs["proposals"][0]
    assert draft.sources[0].transcript_item_id == "answer-1"
    assert draft.sources[0].quoted_text == "It cuts reporting time in half."
    assert draft.sources[0].occurred_at_ms == 1_200


async def test_completed_run_is_reused_without_calling_generator() -> None:
    context = _context()
    service, generator, repository = _service(context)
    run = SimpleNamespace(id=uuid4(), status="completed")
    saved = [SimpleNamespace(id=uuid4())]
    repository.create_run_if_missing = AsyncMock(return_value=(run, False))
    repository.list_proposals_for_run = AsyncMock(return_value=saved)

    outcome = await service.extract_from_interview(
        client_id=context.client_id,
        interview_id=uuid4(),
    )

    assert outcome.run.id == run.id
    assert [proposal.id for proposal in outcome.proposals] == [saved[0].id]
    assert outcome.reused is True
    generator.generate.assert_not_awaited()


async def test_invalid_model_source_marks_run_failed_without_saving() -> None:
    context = _context()
    service, generator, repository = _service(context)
    generator.generate.return_value = ClientKnowledgeGenerationResult(
        proposals=_generated(source_item_id="question-1"),
        model_id="test-model",
    )
    run = SimpleNamespace(id=uuid4(), status="pending", model_id=None)
    claimed = SimpleNamespace(id=run.id, status="processing", model_id=None)
    repository.create_run_if_missing = AsyncMock(return_value=(run, True))
    repository.claim_run_for_processing = AsyncMock(return_value=claimed)

    async def mark_failed(value: Any, *, error_message: str) -> Any:
        value.status = "failed"
        return value

    repository.mark_run_failed = AsyncMock(side_effect=mark_failed)

    outcome = await service.extract_from_interview(
        client_id=context.client_id,
        interview_id=uuid4(),
    )

    assert outcome.run.status == "failed"
    assert outcome.proposals == ()
    repository.mark_run_failed.assert_awaited_once()
    repository.save_proposals.assert_not_called()


async def test_rejects_an_incomplete_interview_before_creating_a_run() -> None:
    context = _context(progress_state="in_progress")
    service, _, repository = _service(context)

    with pytest.raises(AppError) as caught:
        await service.extract_from_interview(
            client_id=context.client_id,
            interview_id=uuid4(),
        )

    assert caught.value.code == "knowledge_interview_not_completed"
    repository.create_run_if_missing.assert_not_called()


async def test_rejects_an_interview_for_another_client() -> None:
    context = _context()
    service, _, _ = _service(context)

    with pytest.raises(AppError) as caught:
        await service.extract_from_interview(
            client_id=uuid4(),
            interview_id=uuid4(),
        )

    assert caught.value.code == "knowledge_interview_not_found"


def test_transcript_hash_is_stable_and_changes_with_content() -> None:
    turns = _context().turns

    first = _transcript_content_hash(turns)
    second = _transcript_content_hash(list(turns))
    changed = _transcript_content_hash(
        [*turns[:-1], turns[-1].model_copy(update={"text": "Different answer"})]
    )

    assert first == second
    assert len(first) == 64
    assert changed != first
