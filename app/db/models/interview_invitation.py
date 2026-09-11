from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.article import Article
    from app.db.models.workspace import Workspace

INVITATION_STATUSES = ("active", "revoked")
PROGRESS_STATES = ("not_opened", "opened", "in_progress", "completed")


class InterviewInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "interview_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_interview_invitations_status",
        ),
        CheckConstraint(
            "progress_state IN ('not_opened', 'opened', 'in_progress', 'completed')",
            name="ck_interview_invitations_progress_state",
        ),
        Index("ix_interview_invitations_article_id", "article_id"),
        Index("ix_interview_invitations_workspace_id", "workspace_id"),
        Index("uq_interview_invitations_token", "token", unique=True),
    )

    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    participant_name: Mapped[str] = mapped_column(String(120), nullable=False)
    participant_email: Mapped[str] = mapped_column(String(254), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_state: Mapped[str] = mapped_column(String(16), nullable=False, default="not_opened")
    questions_answered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    article: Mapped[Article] = relationship(back_populates="invitations", lazy="selectin")
    workspace: Mapped[Workspace] = relationship(lazy="selectin")
