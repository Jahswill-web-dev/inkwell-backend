from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ARTICLE_GOALS = (
    "inform_and_inspire",
    "educate_with_practical_guidance",
    "persuade_or_change_a_perspective",
    "inspire_readers_to_take_action",
    "entertain_with_a_compelling_story",
)


class Article(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint(
            "article_goal IN ("
            "'inform_and_inspire', "
            "'educate_with_practical_guidance', "
            "'persuade_or_change_a_perspective', "
            "'inspire_readers_to_take_action', "
            "'entertain_with_a_compelling_story'"
            ")",
            name="ck_articles_article_goal",
        ),
        CheckConstraint(
            "cardinality(target_audience) BETWEEN 1 AND 10",
            name="ck_articles_target_audience_count",
        ),
        CheckConstraint(
            "array_position(target_audience, NULL) IS NULL",
            name="ck_articles_target_audience_no_nulls",
        ),
        Index("ix_articles_user_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    working_title: Mapped[str] = mapped_column(String(200), nullable=False)
    target_audience: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False)
    article_goal: Mapped[str] = mapped_column(String(64), nullable=False)
