"""Add outline section IDs and article drafts.

Revision ID: 20260821_0008
Revises: 20260818_0007
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260821_0008"
down_revision: str | None = "20260818_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE article_outlines AS outline
        SET sections = updated.sections
        FROM (
            SELECT source.id,
                   jsonb_agg(
                       jsonb_set(section.value, '{id}', to_jsonb(gen_random_uuid()::text), true)
                       ORDER BY section.ordinality
                   ) AS sections
            FROM article_outlines AS source
            CROSS JOIN LATERAL jsonb_array_elements(source.sections)
                WITH ORDINALITY AS section(value, ordinality)
            GROUP BY source.id
        ) AS updated
        WHERE outline.id = updated.id
        """
    )
    op.create_table(
        "article_drafts",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.UniqueConstraint("article_id", name="uq_article_drafts_article_id"),
    )


def downgrade() -> None:
    op.drop_table("article_drafts")
    op.execute(
        """
        UPDATE article_outlines
        SET sections = COALESCE(
            (SELECT jsonb_agg(section.value - 'id' ORDER BY section.ordinality)
             FROM jsonb_array_elements(article_outlines.sections)
                 WITH ORDINALITY AS section(value, ordinality)),
            '[]'::jsonb
        )
        """
    )
