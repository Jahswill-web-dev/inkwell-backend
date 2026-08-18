from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ArticleOutline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "article_outlines"

    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    source_brief_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_token_count: Mapped[int | None] = mapped_column(Integer)
    output_token_count: Mapped[int | None] = mapped_column(Integer)
    generation_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
