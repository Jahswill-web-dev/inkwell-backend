"""Remove the reusable consultant voice profile.

Revision ID: 20261018_0017
Revises: 20261017_0016
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261018_0017"
down_revision = "20261017_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("consultant_profiles", "suggested_voice")
    op.drop_column("consultant_profiles", "voice_revision")
    op.drop_column("consultant_profiles", "voice")


def downgrade() -> None:
    op.add_column(
        "consultant_profiles",
        sa.Column("voice", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "consultant_profiles",
        sa.Column("voice_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("consultant_profiles", sa.Column("suggested_voice", pg.JSONB(), nullable=True))
    op.alter_column("consultant_profiles", "voice", server_default=None)
    op.alter_column("consultant_profiles", "voice_revision", server_default=None)
