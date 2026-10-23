"""Add persistent consultant voice interview phases.

Revision ID: 20261023_0020
Revises: 20261022_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261023_0020"
down_revision: str | None = "20261022_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "consultant_interviews",
        sa.Column("voice_phase", sa.String(length=48), nullable=True),
    )
    op.add_column(
        "consultant_interviews",
        sa.Column(
            "voice_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE consultant_interviews AS interview
        SET voice_phase = CASE
            WHEN interview.state = 'completed' THEN 'completed'
            WHEN EXISTS (
                SELECT 1
                FROM interview_transcripts AS transcript
                WHERE transcript.consultant_interview_id = interview.id
                  AND jsonb_array_length(transcript.turns) > 0
            ) THEN 'awaiting_direction_confirmation'
            ELSE 'awaiting_readiness'
        END
        """
    )
    op.execute(
        """
        UPDATE consultant_interviews
        SET voice_state = jsonb_build_object(
            'version', 0,
            'legacy_direction_reconfirmation', true
        )
        WHERE voice_phase = 'awaiting_direction_confirmation'
        """
    )


def downgrade() -> None:
    op.drop_column("consultant_interviews", "voice_state")
    op.drop_column("consultant_interviews", "voice_phase")
