from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client
    from app.db.models.interview_invitation import InterviewInvitation
    from app.db.models.user import User

ARTICLE_GOALS = (
    "inform_and_inspire",
    "educate_with_practical_guidance",
    "persuade_or_change_a_perspective",
    "inspire_readers_to_take_action",
    "entertain_with_a_compelling_story",
)

ARTICLE_STATUSES = (
    "setup",
    "waiting_for_client",
    "interview_in_progress",
    "ready_to_draft",
    "drafting",
    "in_review",
    "ready_to_publish",
    "published",
)
CONTENT_TYPES = ("blog_post", "thought_leadership", "case_study", "guide", "landing_page")
TARGET_LENGTHS = ("short", "standard", "long")
INTERVIEW_METHODS = ("client", "self", "notes")


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
        Index("ix_articles_workspace_id", "workspace_id"),
        Index("ix_articles_client_id", "client_id"),
        Index("ix_articles_assignee_id", "assignee_id"),
        CheckConstraint(
            "status IN ('setup', 'waiting_for_client', 'interview_in_progress', "
            "'ready_to_draft', 'drafting', 'in_review', 'ready_to_publish', 'published')",
            name="ck_articles_status",
        ),
        CheckConstraint(
            "content_type IN ('blog_post', 'thought_leadership', 'case_study', 'guide', "
            "'landing_page')",
            name="ck_articles_content_type",
        ),
        CheckConstraint(
            "target_length IN ('short', 'standard', 'long')",
            name="ck_articles_target_length",
        ),
        CheckConstraint(
            "interview_method IN ('client', 'self', 'notes')",
            name="ck_articles_interview_method",
        ),
        CheckConstraint(
            "(status = 'published' AND published_at IS NOT NULL) OR "
            "(status <> 'published' AND published_at IS NULL)",
            name="ck_articles_published_state",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    working_title: Mapped[str] = mapped_column(String(200), nullable=False)
    target_audience: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False)
    article_goal: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="setup")
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="blog_post")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_length: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    interview_method: Mapped[str] = mapped_column(String(16), nullable=False, default="notes")
    interviewee_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    interview_instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    main_angle: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    call_to_action: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    tone: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    seo_keyword: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    draft_readiness: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client | None] = relationship(back_populates="articles", lazy="selectin")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="selectin")
    invitations: Mapped[list[InterviewInvitation]] = relationship(
        back_populates="article", cascade="all, delete-orphan", lazy="selectin"
    )
