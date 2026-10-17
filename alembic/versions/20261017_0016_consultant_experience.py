"""Add consultant workspaces, interviews, voice profiles and durable generation.

Revision ID: 20261017_0016
Revises: 20261006_0015
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261017_0016"
down_revision = "20261006_0015"
branch_labels = None
depends_on = None


def base():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def fk(name, table, **kw):
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), **kw)


def upgrade():
    for table, field in [
        ("users", "preferred_experience"),
        ("workspaces", "experience"),
        ("articles", "experience"),
    ]:
        op.add_column(
            table, sa.Column(field, sa.String(24), nullable=False, server_default="marketing")
        )
        op.create_check_constraint(
            f"ck_{table}_{field}", table, f"{field} IN ('marketing', 'consultant')"
        )
    op.add_column(
        "workspaces",
        sa.Column(
            "personal_owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_workspaces_personal_owner", "workspaces", ["personal_owner_id"])
    op.create_table(
        "consultant_profiles",
        *base(),
        fk("user_id", "users", nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("specialty", sa.String(500), nullable=False),
        sa.Column("audience", sa.String(500), nullable=False),
        sa.Column("onboarding_complete", sa.Boolean(), nullable=False),
        sa.Column("voice", pg.JSONB(), nullable=False),
        sa.Column("voice_revision", sa.Integer(), nullable=False),
        sa.Column("suggested_voice", pg.JSONB(), nullable=True),
    )
    op.create_table(
        "consultant_interviews",
        *base(),
        fk("article_id", "articles", nullable=False),
        fk("user_id", "users", nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("questions", pg.JSONB(), nullable=False),
        sa.Column("corrections", pg.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_index("ix_consultant_interviews_article_id", "consultant_interviews", ["article_id"])
    for table, name in [
        ("voice_sessions", "voice_session"),
        ("interview_transcripts", "interview_transcript"),
    ]:
        op.alter_column(table, "invitation_id", existing_type=sa.Uuid(), nullable=True)
        op.add_column(table, fk("consultant_interview_id", "consultant_interviews", nullable=True))
        op.create_index(
            f"ix_{table}_consultant_interview_id",
            table,
            ["consultant_interview_id"],
            unique=table == "interview_transcripts",
        )
        op.create_check_constraint(
            f"ck_{name}_owner",
            table,
            "(invitation_id IS NULL) <> (consultant_interview_id IS NULL)",
        )
    op.create_table(
        "consultant_generations",
        *base(),
        fk("article_id", "articles", nullable=False),
        fk("interview_id", "consultant_interviews", nullable=False),
        sa.Column("request_key", sa.String(120), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("evidence", pg.JSONB()),
        sa.Column("candidate", pg.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("usage", pg.JSONB(), nullable=False),
        sa.UniqueConstraint("article_id", "request_key", name="uq_consultant_generation_request"),
    )
    op.create_index(
        "ix_consultant_generations_article_id", "consultant_generations", ["article_id"]
    )
    op.create_index("ix_consultant_generations_state", "consultant_generations", ["state"])
    op.create_table(
        "consultant_draft_revisions",
        *base(),
        fk("article_id", "articles", nullable=False),
        sa.Column(
            "generation_id",
            sa.Uuid(),
            sa.ForeignKey("consultant_generations.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("sections", pg.JSONB(), nullable=False),
        sa.Column("gaps", pg.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_consultant_draft_revisions_article_id", "consultant_draft_revisions", ["article_id"]
    )


def downgrade():
    # Refuse destructive rollback of consultant data; feature-flag rollback is supported.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM articles WHERE experience='consultant')")
    ):
        raise RuntimeError(
            "Export/remove consultant articles before schema downgrade; disable signup "
            "for a safe rollback."
        )
    op.drop_table("consultant_draft_revisions")
    op.drop_table("consultant_generations")
    for table, name in [
        ("voice_sessions", "voice_session"),
        ("interview_transcripts", "interview_transcript"),
    ]:
        op.drop_constraint(f"ck_{name}_owner", table)
        op.drop_column(table, "consultant_interview_id")
        op.alter_column(table, "invitation_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("consultant_interviews")
    op.drop_table("consultant_profiles")
    op.drop_column("workspaces", "personal_owner_id")
    for table, field in [
        ("users", "preferred_experience"),
        ("workspaces", "experience"),
        ("articles", "experience"),
    ]:
        op.drop_constraint(f"ck_{table}_{field}", table)
        op.drop_column(table, field)
