from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class InterviewTranscript(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "interview_transcripts"
    __table_args__ = (
        CheckConstraint(
            "(invitation_id IS NULL) <> (consultant_interview_id IS NULL)",
            name="ck_interview_transcript_owner",
        ),
        Index("uq_interview_transcripts_invitation_id", "invitation_id", unique=True),
    )

    consultant_interview_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consultant_interviews.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    invitation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("interview_invitations.id", ondelete="CASCADE"), nullable=True
    )
    turns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    insight_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    insights: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_error: Mapped[str | None] = mapped_column(String(240), nullable=True)
