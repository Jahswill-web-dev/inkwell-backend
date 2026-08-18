"""Create article briefs table.

Revision ID: 20260814_0006
Revises: 20260813_0005
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260814_0006"
down_revision: str | None = "20260813_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "article_briefs",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("core_angle", sa.Text(), nullable=False),
        sa.Column("audience_insights", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("tone_and_style", sa.Text(), nullable=False),
        sa.Column("key_takeaways", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("outline", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_gaps", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("call_to_action", sa.Text(), nullable=False),
        sa.Column("seo", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_article_updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("article_id", name="uq_article_briefs_article_id"),
    )


def downgrade() -> None:
    op.drop_table("article_briefs")
