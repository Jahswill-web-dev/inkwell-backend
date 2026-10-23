from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConsultantProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consultant_profiles"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str] = mapped_column(String(500), default="")
    audience: Mapped[str] = mapped_column(String(500), default="")
    onboarding_complete: Mapped[bool] = mapped_column(default=False)


class ConsultantInterview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consultant_interviews"
    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(24), default="active")
    voice_phase: Mapped[str | None] = mapped_column(String(48), nullable=True)
    voice_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    corrections: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class ConsultantGeneration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consultant_generations"
    __table_args__ = (
        UniqueConstraint("article_id", "request_key", name="uq_consultant_generation_request"),
        Index("ix_consultant_generations_created_at", "created_at"),
        Index("ix_consultant_generations_model_created", "model_id", "created_at"),
    )
    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    interview_id: Mapped[UUID] = mapped_column(
        ForeignKey("consultant_interviews.id", ondelete="CASCADE")
    )
    request_key: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(24), default="write")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    candidate: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ConsultantDraftRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consultant_draft_revisions"
    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    generation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consultant_generations.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    gaps: Mapped[list[str]] = mapped_column(JSONB, default=list)


class ConsultantDraftFinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consultant_draft_findings"
    __table_args__ = (
        CheckConstraint(
            "(draft_id IS NOT NULL) <> (revision_id IS NOT NULL)",
            name="ck_consultant_draft_findings_one_target",
        ),
        Index("ix_consultant_draft_findings_draft_status", "draft_id", "status"),
        Index("ix_consultant_draft_findings_revision_status", "revision_id", "status"),
    )

    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consultant_generations.id", ondelete="SET NULL"), nullable=True
    )
    draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("article_drafts.id", ondelete="CASCADE"), nullable=True
    )
    revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consultant_draft_revisions.id", ondelete="CASCADE"), nullable=True
    )
    classification: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    section_id: Mapped[UUID] = mapped_column(nullable=False)
    section_index: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_id: Mapped[UUID | None] = mapped_column(nullable=True)
    paragraph_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    source_turn_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    external_source_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    suggested_replacement: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    resolution_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)


class ConsultantGenerationEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "consultant_generation_events"
    __table_args__ = (
        Index("ix_consultant_generation_events_job_time", "generation_id", "started_at"),
        Index("ix_consultant_generation_events_kind_time", "kind", "started_at"),
    )

    generation_id: Mapped[UUID] = mapped_column(
        ForeignKey("consultant_generations.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    operation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
