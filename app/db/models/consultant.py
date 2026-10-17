from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    voice: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    voice_revision: Mapped[int] = mapped_column(Integer, default=0)
    suggested_voice: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class ConsultantInterview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consultant_interviews"
    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(24), default="active")
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    corrections: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class ConsultantGeneration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consultant_generations"
    __table_args__ = (
        UniqueConstraint("article_id", "request_key", name="uq_consultant_generation_request"),
    )
    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    interview_id: Mapped[UUID] = mapped_column(
        ForeignKey("consultant_interviews.id", ondelete="CASCADE")
    )
    request_key: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(24), default="understand")
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
