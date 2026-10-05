from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.schemas.interview_transcript import GeneratedInterviewInsights
from app.services.interview_transcript_service import (
    InterviewInsightsResult,
    InterviewTranscriptService,
)


@pytest.mark.parametrize("failure", ["unknown_source", "unexpected_error"])
async def test_insight_failure_does_not_discard_completed_interview(failure: str) -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    article = SimpleNamespace(status="waiting_for_client", working_title="Interview article")
    invitation = SimpleNamespace(
        id=uuid4(), progress_state="in_progress", completed_at=None, article=article
    )
    transcript = SimpleNamespace(
        turns=[{"item_id": "participant-1", "speaker": "participant", "text": "An answer"}],
        insight_status="pending",
        generation_error=None,
        insights=None,
        model_id=None,
    )
    generator = MagicMock()
    if failure == "unknown_source":
        insights = GeneratedInterviewInsights(
            summary="Summary",
            key_insights=[{"text": "An insight", "source_item_ids": ["missing-id"]}],
            examples_and_evidence=[],
            claims_to_verify=[],
            open_questions=[],
        )
        generator.generate = AsyncMock(
            return_value=InterviewInsightsResult(insights=insights, model_id="test-model")
        )
    else:
        generator.generate = AsyncMock(side_effect=ValueError("Malformed model output"))

    service = InterviewTranscriptService(session, generator=generator)
    service.invitations.get_by_token = AsyncMock(return_value=invitation)
    service._get_or_create = AsyncMock(return_value=transcript)
    with patch(
        "app.services.interview_transcript_service.transcript_response", return_value="saved"
    ):
        result = await service.finalize(token="test-token")

    assert result == "saved"
    assert invitation.progress_state == "completed"
    assert invitation.completed_at is not None
    assert article.status == "ready_to_draft"
    assert transcript.insight_status == "failed"
    assert transcript.generation_error
    session.flush.assert_awaited()


async def test_writer_can_load_saved_transcript_before_completion() -> None:
    service = InterviewTranscriptService(MagicMock())
    invitation_id = uuid4()
    workspace_id = uuid4()
    transcript = SimpleNamespace(turns=[{"item_id": "saved-1", "speaker": "participant"}])
    service.invitations.get_by_id_in_workspace = AsyncMock(
        return_value=SimpleNamespace(id=invitation_id)
    )
    service._get = AsyncMock(return_value=transcript)

    with patch(
        "app.services.interview_transcript_service.transcript_response", return_value="saved"
    ):
        result = await service.get_for_writer(
            invitation_id=invitation_id, workspace_id=workspace_id
        )

    assert result == "saved"
    service.invitations.get_by_id_in_workspace.assert_awaited_once_with(
        invitation_id, workspace_id
    )
    service._get.assert_awaited_once_with(invitation_id)
