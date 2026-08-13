"""Create articles table.

Revision ID: 20260812_0004
Revises: 20260811_0003
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0004"
down_revision: str | None = "20260811_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "articles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("working_title", sa.String(length=200), nullable=False),
        sa.Column("target_audience", sa.String(length=500), nullable=False),
        sa.Column("article_goal", sa.String(length=64), nullable=False),
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
            "article_goal IN ("
            "'inform_and_inspire', "
            "'educate_with_practical_guidance', "
            "'persuade_or_change_a_perspective', "
            "'inspire_readers_to_take_action', "
            "'entertain_with_a_compelling_story'"
            ")",
            name="ck_articles_article_goal",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_articles_user_id", "articles", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_articles_user_id", table_name="articles")
    op.drop_table("articles")
