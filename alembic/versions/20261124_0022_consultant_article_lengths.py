"""Allow extended and in-depth consultant articles.

Revision ID: 20261124_0022
Revises: 20261027_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261124_0022"
down_revision: str | None = "20261027_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_articles_target_length", "articles", type_="check")
    op.create_check_constraint(
        "ck_articles_target_length",
        "articles",
        "target_length IN ('short', 'standard', 'long') OR "
        "(experience = 'consultant' AND target_length IN ('extended', 'in_depth'))",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM articles WHERE target_length IN ('extended', 'in_depth'))"
        )
    ):
        raise RuntimeError("Cannot downgrade while extended or in-depth articles exist.")
    op.drop_constraint("ck_articles_target_length", "articles", type_="check")
    op.create_check_constraint(
        "ck_articles_target_length", "articles", "target_length IN ('short', 'standard', 'long')"
    )
