from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.client_knowledge import (
    ClientKnowledgeProposalCreate,
    ClientKnowledgeProposalReview,
    GeneratedKnowledgeProposal,
    GeneratedKnowledgeProposals,
)


def test_generated_proposals_accept_the_supported_taxonomy() -> None:
    result = GeneratedKnowledgeProposals.model_validate(
        {
            "proposals": [
                {
                    "category": "proof_points_and_results",
                    "statement": "Customers reduce onboarding time by 40%.",
                    "knowledge_type": "fact",
                    "source_item_ids": ["turn-12"],
                    "confidence": 0.82,
                    "needs_verification": True,
                }
            ]
        }
    )

    assert result.proposals[0].proposed_action == "create"
    assert result.proposals[0].needs_verification is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "miscellaneous"},
        {"statement": "   "},
        {"knowledge_type": "guess"},
        {"source_item_ids": []},
        {"source_item_ids": ["same", "same"]},
        {"confidence": 1.1},
    ],
)
def test_generated_proposal_rejects_untrusted_values(overrides: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "category": "company",
        "statement": "The company was founded in 2018.",
        "knowledge_type": "fact",
        "source_item_ids": ["turn-1"],
    }
    payload.update(overrides)

    with pytest.raises(ValidationError):
        GeneratedKnowledgeProposal.model_validate(payload)


def test_persistence_input_requires_unique_resolved_sources() -> None:
    with pytest.raises(ValidationError):
        ClientKnowledgeProposalCreate.model_validate(
            {
                "category": "expert_perspectives",
                "statement": "Human review should remain part of the process.",
                "knowledge_type": "opinion",
                "valid_until": datetime.now(UTC),
                "sources": [
                    {"transcript_item_id": "turn-4", "quoted_text": "First"},
                    {"transcript_item_id": "turn-4", "quoted_text": "Duplicate"},
                ],
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "edited_and_approved"},
        {"decision": "approved", "edited_statement": "Unexpected edit"},
        {"decision": "rejected", "edited_statement": "Unexpected edit"},
        {"decision": "edited_and_approved", "edited_statement": "   "},
    ],
)
def test_review_payload_rejects_invalid_edit_combinations(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ClientKnowledgeProposalReview.model_validate(payload)


def test_review_payload_normalizes_an_approved_edit() -> None:
    review = ClientKnowledgeProposalReview.model_validate(
        {
            "decision": "edited_and_approved",
            "edited_statement": "  The approved statement.  ",
            "review_notes": "  Clarified during review.  ",
        }
    )

    assert review.edited_statement == "The approved statement."
    assert review.review_notes == "Clarified during review."
