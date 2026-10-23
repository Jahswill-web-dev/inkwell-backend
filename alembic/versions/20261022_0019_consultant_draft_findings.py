"""Persist consultant draft fidelity findings.

Revision ID: 20261022_0019
Revises: 20261021_0018
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261022_0019"
down_revision = "20261021_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultant_draft_findings",
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("classification", sa.String(length=24), nullable=False),
        sa.Column("category", sa.String(length=48), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_index", sa.Integer(), nullable=False),
        sa.Column("paragraph_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("paragraph_index", sa.Integer(), nullable=True),
        sa.Column("claim_text", sa.Text(), server_default="", nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source_turn_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("external_source_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suggested_replacement", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="open", nullable=False),
        sa.Column("resolution_reason", sa.String(length=48), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(draft_id IS NOT NULL) <> (revision_id IS NOT NULL)",
            name="ck_consultant_draft_findings_one_target",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["generation_id"], ["consultant_generations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["article_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["consultant_draft_revisions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_consultant_draft_findings_article_id", "consultant_draft_findings", ["article_id"]
    )
    op.create_index(
        "ix_consultant_draft_findings_draft_status",
        "consultant_draft_findings",
        ["draft_id", "status"],
    )
    op.create_index(
        "ix_consultant_draft_findings_revision_status",
        "consultant_draft_findings",
        ["revision_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consultant_draft_findings_revision_status", table_name="consultant_draft_findings"
    )
    op.drop_index(
        "ix_consultant_draft_findings_draft_status", table_name="consultant_draft_findings"
    )
    op.drop_index("ix_consultant_draft_findings_article_id", table_name="consultant_draft_findings")
    op.drop_table("consultant_draft_findings")
