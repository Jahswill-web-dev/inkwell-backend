"""Separate article outlines from briefs.

Revision ID: 20260818_0007
Revises: 20260814_0006
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260818_0007"
down_revision: str | None = "20260814_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "article_briefs",
        sa.Column("outline_revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.alter_column("article_briefs", "outline_revision", server_default=None)
    op.create_table(
        "article_outlines",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_brief_revision", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("input_token_count", sa.Integer(), nullable=True),
        sa.Column("output_token_count", sa.Integer(), nullable=True),
        sa.Column("generation_duration_ms", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", name="uq_article_outlines_article_id"),
    )
    op.drop_column("article_briefs", "outline")


def downgrade() -> None:
    op.add_column(
        "article_briefs",
        sa.Column("outline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        "UPDATE article_briefs AS brief SET outline = outline.sections "
        "FROM article_outlines AS outline WHERE outline.article_id = brief.article_id"
    )
    op.execute("UPDATE article_briefs SET outline = '[]'::jsonb WHERE outline IS NULL")
    op.alter_column("article_briefs", "outline", nullable=False)
    op.drop_table("article_outlines")
    op.drop_column("article_briefs", "outline_revision")
