"""Create interview_invitations table for guest interview links and sessions.

Revision ID: 20260910_0011
Revises: 20260910_0010
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260910_0011"
down_revision: str | None = "20260910_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("participant_name", sa.String(length=120), nullable=False),
        sa.Column("participant_email", sa.String(length=254), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "progress_state", sa.String(length=16), server_default="not_opened", nullable=False
        ),
        sa.Column("questions_answered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_questions", sa.Integer(), server_default="6", nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "session_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_interview_invitations_status",
        ),
        sa.CheckConstraint(
            "progress_state IN ('not_opened', 'opened', 'in_progress', 'completed')",
            name="ck_interview_invitations_progress_state",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_invitations_article_id",
        "interview_invitations",
        ["article_id"],
        unique=False,
    )
    op.create_index(
        "ix_interview_invitations_workspace_id",
        "interview_invitations",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_interview_invitations_token",
        "interview_invitations",
        ["token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_interview_invitations_token", table_name="interview_invitations")
    op.drop_index("ix_interview_invitations_workspace_id", table_name="interview_invitations")
    op.drop_index("ix_interview_invitations_article_id", table_name="interview_invitations")
    op.drop_table("interview_invitations")
