"""Create persisted voice-interview transcripts and insight notes.

Revision ID: 20260915_0012
Revises: 20260910_0011
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260915_0012"
down_revision: str | None = "20260910_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_transcripts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invitation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "turns", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("insight_status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("insights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model_id", sa.String(length=120), nullable=True),
        sa.Column("generation_error", sa.String(length=240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["invitation_id"], ["interview_invitations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_interview_transcripts_invitation_id", "interview_transcripts", ["invitation_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_interview_transcripts_invitation_id", table_name="interview_transcripts")
    op.drop_table("interview_transcripts")
