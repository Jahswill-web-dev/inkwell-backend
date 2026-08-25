from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SectionInterview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "section_interviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_answers', 'generated')",
            name="ck_section_interviews_status",
        ),
        Index("ix_section_interviews_draft_section", "draft_id", "section_id"),
    )

    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("article_drafts.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    generated_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    question_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    question_input_token_count: Mapped[int | None] = mapped_column(Integer)
    question_output_token_count: Mapped[int | None] = mapped_column(Integer)
    question_generation_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    draft_model_id: Mapped[str | None] = mapped_column(String(128))
    draft_prompt_version: Mapped[str | None] = mapped_column(String(64))
    draft_input_token_count: Mapped[int | None] = mapped_column(Integer)
    draft_output_token_count: Mapped[int | None] = mapped_column(Integer)
    draft_generation_duration_ms: Mapped[int | None] = mapped_column(Integer)
