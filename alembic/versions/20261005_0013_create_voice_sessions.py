"""create voice sessions

Revision ID: 20261005_0013
Revises: 20260915_0012
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261005_0013"
down_revision: str | None = "20260915_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voice_sessions",
        sa.Column("invitation_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("external_session_id", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("termination_reason", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "provider_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["interview_invitations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_voice_sessions_invitation_id", "voice_sessions", ["invitation_id"], unique=False
    )
    op.create_index(
        "uq_voice_sessions_provider_external_session_id",
        "voice_sessions",
        ["provider", "external_session_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_voice_sessions_provider_external_session_id", table_name="voice_sessions"
    )
    op.drop_index("ix_voice_sessions_invitation_id", table_name="voice_sessions")
    op.drop_table("voice_sessions")
