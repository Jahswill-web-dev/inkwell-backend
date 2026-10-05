from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class VoiceSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "voice_sessions"
    __table_args__ = (
        Index("ix_voice_sessions_invitation_id", "invitation_id"),
        Index(
            "uq_voice_sessions_provider_external_session_id",
            "provider",
            "external_session_id",
            unique=True,
        ),
    )

    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_invitations.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    external_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    termination_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
