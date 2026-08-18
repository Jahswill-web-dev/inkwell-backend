from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ArticleBrief(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "article_briefs"

    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    core_angle: Mapped[str] = mapped_column(Text, nullable=False)
    audience_insights: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    tone_and_style: Mapped[str] = mapped_column(Text, nullable=False)
    key_takeaways: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    evidence_gaps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    call_to_action: Mapped[str] = mapped_column(Text, nullable=False)
    seo: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_article_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_token_count: Mapped[int | None] = mapped_column(Integer)
    output_token_count: Mapped[int | None] = mapped_column(Integer)
    generation_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
