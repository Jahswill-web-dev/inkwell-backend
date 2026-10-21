"""Persist consultant generation performance events.

Revision ID: 20261021_0018
Revises: 20261018_0017
"""

import sqlalchemy as sa

from alembic import op

revision = "20261021_0018"
down_revision = "20261018_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_consultant_generations_created_at", "consultant_generations", ["created_at"]
    )
    op.create_index(
        "ix_consultant_generations_model_created",
        "consultant_generations",
        ["model_id", "created_at"],
    )
    op.create_table(
        "consultant_generation_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "generation_id",
            sa.Uuid(),
            sa.ForeignKey("consultant_generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("operation", sa.String(80)),
        sa.Column("model_id", sa.String(128)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("provider_request_id", sa.String(128)),
    )
    op.create_index(
        "ix_consultant_generation_events_job_time",
        "consultant_generation_events",
        ["generation_id", "started_at"],
    )
    op.create_index(
        "ix_consultant_generation_events_kind_time",
        "consultant_generation_events",
        ["kind", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_consultant_generation_events_kind_time", "consultant_generation_events")
    op.drop_index("ix_consultant_generation_events_job_time", "consultant_generation_events")
    op.drop_table("consultant_generation_events")
    op.drop_index("ix_consultant_generations_model_created", "consultant_generations")
    op.drop_index("ix_consultant_generations_created_at", "consultant_generations")
