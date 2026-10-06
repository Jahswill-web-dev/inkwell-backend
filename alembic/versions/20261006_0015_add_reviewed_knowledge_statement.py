"""Preserve reviewer edits separately from extracted knowledge proposals.

Revision ID: 20261006_0015
Revises: 20261006_0014
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261006_0015"
down_revision: str | None = "20261006_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "client_knowledge_proposals",
        sa.Column("reviewed_statement", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_knowledge_proposals_reviewed_statement_not_blank",
        "client_knowledge_proposals",
        "reviewed_statement IS NULL OR char_length(btrim(reviewed_statement)) > 0",
    )
    op.create_check_constraint(
        "ck_knowledge_proposals_reviewed_statement_status",
        "client_knowledge_proposals",
        "reviewed_statement IS NULL OR review_status IN ('approved', 'applied')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_knowledge_proposals_reviewed_statement_status",
        "client_knowledge_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_proposals_reviewed_statement_not_blank",
        "client_knowledge_proposals",
        type_="check",
    )
    op.drop_column("client_knowledge_proposals", "reviewed_statement")
