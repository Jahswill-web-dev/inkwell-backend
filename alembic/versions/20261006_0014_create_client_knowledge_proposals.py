"""Create the client knowledge proposal pipeline.

Revision ID: 20261006_0014
Revises: 20261005_0013
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261006_0014"
down_revision: str | None = "20261005_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_knowledge_extraction_runs",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("transcript_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("model_id", sa.String(length=120), nullable=True),
        sa.Column("transcript_content_hash", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_knowledge_extraction_runs_status",
        ),
        sa.CheckConstraint(
            "char_length(transcript_content_hash) = 64",
            name="ck_knowledge_extraction_runs_content_hash",
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transcript_id"], ["interview_transcripts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcript_id",
            "prompt_version",
            "transcript_content_hash",
            name="uq_knowledge_extraction_runs_transcript_version_hash",
        ),
    )
    op.create_index(
        "ix_knowledge_extraction_runs_workspace_client",
        "client_knowledge_extraction_runs",
        ["workspace_id", "client_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_extraction_runs_status",
        "client_knowledge_extraction_runs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "client_knowledge_proposals",
        sa.Column("extraction_run_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("knowledge_type", sa.String(length=24), nullable=False),
        sa.Column("proposed_action", sa.String(length=16), server_default="create", nullable=False),
        sa.Column(
            "review_status", sa.String(length=24), server_default="pending_review", nullable=False
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "needs_verification", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "category IN ("
            "'company', 'products_and_services', 'audience_and_customers', "
            "'problems_and_needs', 'positioning_and_differentiation', "
            "'messaging_and_terminology', 'proof_points_and_results', "
            "'stories_and_examples', 'expert_perspectives', "
            "'processes_and_frameworks'"
            ")",
            name="ck_knowledge_proposals_category",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_knowledge_proposals_confidence",
        ),
        sa.CheckConstraint(
            "char_length(btrim(statement)) > 0",
            name="ck_knowledge_proposals_statement_not_blank",
        ),
        sa.CheckConstraint(
            "knowledge_type IN ('fact', 'opinion', 'instruction', 'example')",
            name="ck_knowledge_proposals_type",
        ),
        sa.CheckConstraint(
            "proposed_action IN ('create', 'update', 'reinforce', 'conflict')",
            name="ck_knowledge_proposals_action",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending_review', 'approved', 'rejected', 'applied')",
            name="ck_knowledge_proposals_review_status",
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["extraction_run_id"],
            ["client_knowledge_extraction_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_proposals_extraction_run_id",
        "client_knowledge_proposals",
        ["extraction_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_proposals_client_review_status",
        "client_knowledge_proposals",
        ["client_id", "review_status"],
        unique=False,
    )

    op.create_table(
        "client_knowledge_sources",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_type", sa.String(length=24), server_default="interview_turn", nullable=False
        ),
        sa.Column("transcript_id", sa.Uuid(), nullable=True),
        sa.Column("transcript_item_id", sa.String(length=200), nullable=True),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("interviewee_name", sa.String(length=120), nullable=True),
        sa.Column("quoted_text", sa.Text(), nullable=False),
        sa.Column("occurred_at_ms", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "occurred_at_ms IS NULL OR occurred_at_ms >= 0",
            name="ck_knowledge_sources_occurred_at_ms",
        ),
        sa.CheckConstraint(
            "char_length(btrim(quoted_text)) > 0",
            name="ck_knowledge_sources_quoted_text_not_blank",
        ),
        sa.CheckConstraint(
            "source_type <> 'interview_turn' OR "
            "(transcript_id IS NOT NULL AND transcript_item_id IS NOT NULL)",
            name="ck_knowledge_sources_interview_reference",
        ),
        sa.CheckConstraint(
            "source_type IN ('interview_turn', 'manual_note', 'document', 'website')",
            name="ck_knowledge_sources_type",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transcript_id"], ["interview_transcripts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcript_id",
            "transcript_item_id",
            name="uq_knowledge_sources_transcript_item",
        ),
    )
    op.create_index(
        "ix_knowledge_sources_workspace_client",
        "client_knowledge_sources",
        ["workspace_id", "client_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_sources_article_id",
        "client_knowledge_sources",
        ["article_id"],
        unique=False,
    )

    op.create_table(
        "client_knowledge_proposal_sources",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", sa.String(length=16), server_default="supports", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "relationship IN ('supports', 'contradicts', 'supersedes')",
            name="ck_knowledge_proposal_sources_relationship",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["client_knowledge_proposals.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["client_knowledge_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("proposal_id", "source_id"),
    )
    op.create_index(
        "ix_knowledge_proposal_sources_source_id",
        "client_knowledge_proposal_sources",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_proposal_sources_source_id",
        table_name="client_knowledge_proposal_sources",
    )
    op.drop_table("client_knowledge_proposal_sources")

    op.drop_index("ix_knowledge_sources_article_id", table_name="client_knowledge_sources")
    op.drop_index("ix_knowledge_sources_workspace_client", table_name="client_knowledge_sources")
    op.drop_table("client_knowledge_sources")

    op.drop_index(
        "ix_knowledge_proposals_client_review_status",
        table_name="client_knowledge_proposals",
    )
    op.drop_index(
        "ix_knowledge_proposals_extraction_run_id",
        table_name="client_knowledge_proposals",
    )
    op.drop_table("client_knowledge_proposals")

    op.drop_index(
        "ix_knowledge_extraction_runs_status",
        table_name="client_knowledge_extraction_runs",
    )
    op.drop_index(
        "ix_knowledge_extraction_runs_workspace_client",
        table_name="client_knowledge_extraction_runs",
    )
    op.drop_table("client_knowledge_extraction_runs")
