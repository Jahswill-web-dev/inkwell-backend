from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ArticleDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "article_drafts"

    article_id: Mapped[UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
