from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

KnowledgeCategory = Literal[
    "company",
    "products_and_services",
    "audience_and_customers",
    "problems_and_needs",
    "positioning_and_differentiation",
    "messaging_and_terminology",
    "proof_points_and_results",
    "stories_and_examples",
    "expert_perspectives",
    "processes_and_frameworks",
]
KnowledgeType = Literal["fact", "opinion", "instruction", "example"]
ProposalAction = Literal["create", "update", "reinforce", "conflict"]
ProposalReviewStatus = Literal["pending_review", "approved", "rejected", "applied"]
ProposalReviewDecision = Literal["approved", "rejected", "edited_and_approved"]
ExtractionRunStatus = Literal["pending", "processing", "completed", "failed"]
SourceRelationship = Literal["supports", "contradicts", "supersedes"]

Statement = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
TranscriptItemId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
QuotedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
]
ReviewNotes = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]


class GeneratedKnowledgeProposal(BaseModel):
    """Strict output contract for the knowledge extraction model."""

    model_config = ConfigDict(extra="forbid")

    category: KnowledgeCategory
    statement: Statement
    knowledge_type: KnowledgeType
    proposed_action: ProposalAction = "create"
    source_item_ids: Annotated[list[TranscriptItemId], Field(min_length=1, max_length=8)]
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_verification: bool = False
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def require_unique_source_item_ids(self) -> Self:
        if len(self.source_item_ids) != len(set(self.source_item_ids)):
            raise ValueError("Source item IDs must be unique")
        return self


class GeneratedKnowledgeProposals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: Annotated[list[GeneratedKnowledgeProposal], Field(max_length=40)]


class ClientKnowledgeSourceCreate(BaseModel):
    """Validated transcript evidence resolved from an extracted source item ID."""

    model_config = ConfigDict(extra="forbid")

    transcript_item_id: TranscriptItemId
    quoted_text: QuotedText
    occurred_at_ms: int | None = Field(default=None, ge=0)
    relationship: SourceRelationship = "supports"


class ClientKnowledgeProposalCreate(BaseModel):
    """Persistence input after source IDs have been resolved against the transcript."""

    model_config = ConfigDict(extra="forbid")

    category: KnowledgeCategory
    statement: Statement
    knowledge_type: KnowledgeType
    proposed_action: ProposalAction = "create"
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_verification: bool = False
    valid_until: datetime | None = None
    sources: Annotated[list[ClientKnowledgeSourceCreate], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def require_unique_sources(self) -> Self:
        ids = [source.transcript_item_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Proposal sources must be unique")
        return self


class ClientKnowledgeProposalReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ProposalReviewDecision
    edited_statement: Statement | None = None
    review_notes: ReviewNotes | None = None

    @model_validator(mode="after")
    def validate_edited_statement(self) -> Self:
        if self.decision == "edited_and_approved" and self.edited_statement is None:
            raise ValueError("An edited statement is required for edited_and_approved")
        if self.decision != "edited_and_approved" and self.edited_statement is not None:
            raise ValueError("An edited statement is only allowed for edited_and_approved")
        return self


class ClientKnowledgeExtractionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    client_id: UUID
    transcript_id: UUID
    status: ExtractionRunStatus
    prompt_version: str
    model_id: str | None
    transcript_content_hash: str
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ClientKnowledgeProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    extraction_run_id: UUID
    workspace_id: UUID
    client_id: UUID
    category: KnowledgeCategory
    statement: str
    knowledge_type: KnowledgeType
    proposed_action: ProposalAction
    review_status: ProposalReviewStatus
    confidence: float | None
    needs_verification: bool
    valid_until: datetime | None
    review_notes: str | None
    reviewed_statement: str | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ClientKnowledgeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    client_id: UUID
    source_type: Literal["interview_turn", "manual_note", "document", "website"]
    transcript_id: UUID | None
    transcript_item_id: str | None
    article_id: UUID | None
    interviewee_name: str | None
    quoted_text: str
    occurred_at_ms: int | None
    source_url: str | None
    captured_at: datetime
    created_at: datetime
    updated_at: datetime
