from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def _utcnow() -> datetime:
    return datetime.now(UTC)


KNOWLEDGE_CATEGORIES = (
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
)

KNOWLEDGE_TYPES = ("fact", "opinion", "instruction", "example")
EXTRACTION_RUN_STATUSES = ("pending", "processing", "completed", "failed")
PROPOSAL_ACTIONS = ("create", "update", "reinforce", "conflict")
PROPOSAL_REVIEW_STATUSES = ("pending_review", "approved", "rejected", "applied")
KNOWLEDGE_SOURCE_TYPES = ("interview_turn", "manual_note", "document", "website")
SOURCE_RELATIONSHIPS = ("supports", "contradicts", "supersedes")


class ClientKnowledgeExtractionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_knowledge_extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_knowledge_extraction_runs_status",
        ),
        CheckConstraint(
            "char_length(transcript_content_hash) = 64",
            name="ck_knowledge_extraction_runs_content_hash",
        ),
        UniqueConstraint(
            "transcript_id",
            "prompt_version",
            "transcript_content_hash",
            name="uq_knowledge_extraction_runs_transcript_version_hash",
        ),
        Index(
            "ix_knowledge_extraction_runs_workspace_client",
            "workspace_id",
            "client_id",
        ),
        Index("ix_knowledge_extraction_runs_status", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_transcripts.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    transcript_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientKnowledgeProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_knowledge_proposals"
    __table_args__ = (
        CheckConstraint(
            "category IN ("
            "'company', 'products_and_services', 'audience_and_customers', "
            "'problems_and_needs', 'positioning_and_differentiation', "
            "'messaging_and_terminology', 'proof_points_and_results', "
            "'stories_and_examples', 'expert_perspectives', "
            "'processes_and_frameworks'"
            ")",
            name="ck_knowledge_proposals_category",
        ),
        CheckConstraint(
            "knowledge_type IN ('fact', 'opinion', 'instruction', 'example')",
            name="ck_knowledge_proposals_type",
        ),
        CheckConstraint(
            "proposed_action IN ('create', 'update', 'reinforce', 'conflict')",
            name="ck_knowledge_proposals_action",
        ),
        CheckConstraint(
            "review_status IN ('pending_review', 'approved', 'rejected', 'applied')",
            name="ck_knowledge_proposals_review_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_knowledge_proposals_confidence",
        ),
        CheckConstraint(
            "char_length(btrim(statement)) > 0",
            name="ck_knowledge_proposals_statement_not_blank",
        ),
        CheckConstraint(
            "reviewed_statement IS NULL OR char_length(btrim(reviewed_statement)) > 0",
            name="ck_knowledge_proposals_reviewed_statement_not_blank",
        ),
        CheckConstraint(
            "reviewed_statement IS NULL OR review_status IN ('approved', 'applied')",
            name="ck_knowledge_proposals_reviewed_statement_status",
        ),
        Index("ix_knowledge_proposals_extraction_run_id", "extraction_run_id"),
        Index(
            "ix_knowledge_proposals_client_review_status",
            "client_id",
            "review_status",
        ),
    )

    extraction_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("client_knowledge_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_type: Mapped[str] = mapped_column(String(24), nullable=False)
    proposed_action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="create", server_default="create"
    )
    review_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending_review",
        server_default="pending_review",
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_verification: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientKnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_knowledge_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('interview_turn', 'manual_note', 'document', 'website')",
            name="ck_knowledge_sources_type",
        ),
        CheckConstraint(
            "occurred_at_ms IS NULL OR occurred_at_ms >= 0",
            name="ck_knowledge_sources_occurred_at_ms",
        ),
        CheckConstraint(
            "char_length(btrim(quoted_text)) > 0",
            name="ck_knowledge_sources_quoted_text_not_blank",
        ),
        CheckConstraint(
            "source_type <> 'interview_turn' OR "
            "(transcript_id IS NOT NULL AND transcript_item_id IS NOT NULL)",
            name="ck_knowledge_sources_interview_reference",
        ),
        UniqueConstraint(
            "transcript_id",
            "transcript_item_id",
            name="uq_knowledge_sources_transcript_item",
        ),
        Index(
            "ix_knowledge_sources_workspace_client",
            "workspace_id",
            "client_id",
        ),
        Index("ix_knowledge_sources_article_id", "article_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="interview_turn",
        server_default="interview_turn",
    )
    transcript_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("interview_transcripts.id", ondelete="CASCADE"), nullable=True
    )
    transcript_item_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    article_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), nullable=True
    )
    interviewee_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quoted_text: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )


class ClientKnowledgeProposalSource(Base):
    __tablename__ = "client_knowledge_proposal_sources"
    __table_args__ = (
        CheckConstraint(
            "relationship IN ('supports', 'contradicts', 'supersedes')",
            name="ck_knowledge_proposal_sources_relationship",
        ),
        Index("ix_knowledge_proposal_sources_source_id", "source_id"),
    )

    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("client_knowledge_proposals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("client_knowledge_sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relationship: Mapped[str] = mapped_column(
        String(16), nullable=False, default="supports", server_default="supports"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
