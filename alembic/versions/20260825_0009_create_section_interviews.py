"""Create persistent section interviews.

Revision ID: 20260825_0009
Revises: 20260821_0008
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260825_0009"
down_revision: str | None = "20260821_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "section_interviews",
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("question_model_id", sa.String(length=128), nullable=False),
        sa.Column("question_prompt_version", sa.String(length=64), nullable=False),
        sa.Column("question_input_token_count", sa.Integer(), nullable=True),
        sa.Column("question_output_token_count", sa.Integer(), nullable=True),
        sa.Column("question_generation_duration_ms", sa.Integer(), nullable=False),
        sa.Column("draft_model_id", sa.String(length=128), nullable=True),
        sa.Column("draft_prompt_version", sa.String(length=64), nullable=True),
        sa.Column("draft_input_token_count", sa.Integer(), nullable=True),
        sa.Column("draft_output_token_count", sa.Integer(), nullable=True),
        sa.Column("draft_generation_duration_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
            "status IN ('awaiting_answers', 'generated')",
            name="ck_section_interviews_status",
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["article_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_section_interviews_draft_section",
        "section_interviews",
        ["draft_id", "section_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_section_interviews_draft_section", table_name="section_interviews")
    op.drop_table("section_interviews")
