from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import AppError
from app.schemas.client_knowledge import ClientKnowledgeProposalReview
from app.services.client_knowledge_review_service import ClientKnowledgeReviewService


def _service() -> tuple[ClientKnowledgeReviewService, MagicMock, MagicMock]:
    knowledge = MagicMock()
    workspaces = MagicMock()
    return ClientKnowledgeReviewService(knowledge, workspaces), knowledge, workspaces


@pytest.mark.parametrize(
    ("payload", "expected_status", "expected_statement"),
    [
        ({"decision": "approved"}, "approved", None),
        (
            {
                "decision": "edited_and_approved",
                "edited_statement": "A clearer approved statement.",
            },
            "approved",
            "A clearer approved statement.",
        ),
        ({"decision": "rejected", "review_notes": "Not reusable."}, "rejected", None),
    ],
)
async def test_records_supported_review_decisions(
    payload: dict[str, object],
    expected_status: str,
    expected_statement: str | None,
) -> None:
    service, knowledge, workspaces = _service()
    reviewer_id = uuid4()
    proposal_id = uuid4()
    workspace_id = uuid4()
    proposal = SimpleNamespace(id=proposal_id, review_status="pending_review")
    persisted = SimpleNamespace(id=proposal_id, review_status=expected_status)
    workspaces.get_default_for_user = AsyncMock(
        return_value=(SimpleNamespace(id=workspace_id), SimpleNamespace(role="member"))
    )
    knowledge.get_proposal_for_review = AsyncMock(return_value=proposal)
    knowledge.record_proposal_review = AsyncMock(return_value=persisted)

    result = await service.review_proposal(
        proposal_id=proposal_id,
        reviewer_user_id=reviewer_id,
        payload=ClientKnowledgeProposalReview.model_validate(payload),
    )

    assert result is persisted
    knowledge.get_proposal_for_review.assert_awaited_once_with(
        proposal_id=proposal_id,
        workspace_id=workspace_id,
    )
    review = knowledge.record_proposal_review.await_args.kwargs
    assert review["review_status"] == expected_status
    assert review["reviewed_statement"] == expected_statement
    assert review["reviewed_by_user_id"] == reviewer_id


async def test_hides_a_proposal_outside_the_reviewers_workspace() -> None:
    service, knowledge, workspaces = _service()
    workspaces.get_default_for_user = AsyncMock(
        return_value=(SimpleNamespace(id=uuid4()), SimpleNamespace(role="member"))
    )
    knowledge.get_proposal_for_review = AsyncMock(return_value=None)

    with pytest.raises(AppError) as caught:
        await service.review_proposal(
            proposal_id=uuid4(),
            reviewer_user_id=uuid4(),
            payload=ClientKnowledgeProposalReview(decision="approved"),
        )

    assert caught.value.code == "knowledge_proposal_not_found"
    knowledge.record_proposal_review.assert_not_called()


async def test_prevents_a_second_review_decision() -> None:
    service, knowledge, workspaces = _service()
    workspaces.get_default_for_user = AsyncMock(
        return_value=(SimpleNamespace(id=uuid4()), SimpleNamespace(role="member"))
    )
    knowledge.get_proposal_for_review = AsyncMock(
        return_value=SimpleNamespace(id=uuid4(), review_status="approved")
    )

    with pytest.raises(AppError) as caught:
        await service.review_proposal(
            proposal_id=uuid4(),
            reviewer_user_id=uuid4(),
            payload=ClientKnowledgeProposalReview(decision="rejected"),
        )

    assert caught.value.code == "knowledge_proposal_already_reviewed"
    knowledge.record_proposal_review.assert_not_called()


async def test_requires_a_default_workspace() -> None:
    service, knowledge, workspaces = _service()
    workspaces.get_default_for_user = AsyncMock(return_value=None)

    with pytest.raises(AppError) as caught:
        await service.review_proposal(
            proposal_id=uuid4(),
            reviewer_user_id=uuid4(),
            payload=ClientKnowledgeProposalReview(decision="approved"),
        )

    assert caught.value.code == "workspace_not_found"
    knowledge.get_proposal_for_review.assert_not_called()
