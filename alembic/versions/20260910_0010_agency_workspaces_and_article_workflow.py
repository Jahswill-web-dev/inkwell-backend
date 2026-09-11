"""Add agency workspaces, clients, and article workflow metadata.

Revision ID: 20260910_0010
Revises: 20260825_0009
Create Date: 2026-09-10
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260910_0010"
down_revision: str | None = "20260825_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'member')",
            name="ck_workspace_members_role",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_members_workspace_user",
        ),
    )
    op.create_index(
        "ix_workspace_members_workspace_id",
        "workspace_members",
        ["workspace_id"],
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])
    op.create_index(
        "uq_workspace_members_default_user",
        "workspace_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "clients",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("website", sa.String(length=2048), nullable=True),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clients_workspace_id", "clients", ["workspace_id"])
    op.create_index(
        "uq_clients_workspace_name_ci",
        "clients",
        [sa.text("workspace_id"), sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "client_brand_profiles",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("default_audience", sa.Text(), nullable=True),
        sa.Column("brand_voice", sa.Text(), nullable=True),
        sa.Column(
            "preferred_terminology",
            postgresql.ARRAY(sa.String(length=200)),
            nullable=False,
        ),
        sa.Column(
            "avoided_terminology",
            postgresql.ARRAY(sa.String(length=200)),
            nullable=False,
        ),
        sa.Column(
            "default_calls_to_action",
            postgresql.ARRAY(sa.String(length=500)),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_client_brand_profiles_client_id",
        "client_brand_profiles",
        ["client_id"],
        unique=True,
    )

    op.add_column("articles", sa.Column("workspace_id", sa.Uuid(), nullable=True))
    op.add_column("articles", sa.Column("client_id", sa.Uuid(), nullable=True))
    op.add_column("articles", sa.Column("assignee_id", sa.Uuid(), nullable=True))
    op.add_column(
        "articles",
        sa.Column("status", sa.String(length=32), server_default="setup", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("content_type", sa.String(length=32), server_default="blog_post", nullable=False),
    )
    op.add_column("articles", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column(
        "articles",
        sa.Column("target_length", sa.String(length=16), server_default="standard", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("interview_method", sa.String(length=16), server_default="notes", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("interviewee_name", sa.String(length=120), server_default="", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("interview_instructions", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("articles", sa.Column("main_angle", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "articles",
        sa.Column("key_message", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("call_to_action", sa.String(length=500), server_default="", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("tone", sa.String(length=500), server_default="", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column("seo_keyword", sa.String(length=200), server_default="", nullable=False),
    )
    op.add_column(
        "articles",
        sa.Column(
            "draft_readiness",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "articles",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )

    connection = op.get_bind()
    users = connection.execute(sa.text("SELECT id, username FROM users")).mappings().all()
    for user in users:
        workspace_id = uuid4()
        connection.execute(
            sa.text("INSERT INTO workspaces (id, name) VALUES (:id, :name)"),
            {"id": workspace_id, "name": f"{user['username']}'s workspace"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO workspace_members "
                "(id, workspace_id, user_id, role, is_default) "
                "VALUES (:id, :workspace_id, :user_id, 'owner', true)"
            ),
            {
                "id": uuid4(),
                "workspace_id": workspace_id,
                "user_id": user["id"],
            },
        )
        connection.execute(
            sa.text(
                "UPDATE articles SET workspace_id = :workspace_id, assignee_id = user_id "
                "WHERE user_id = :user_id"
            ),
            {"workspace_id": workspace_id, "user_id": user["id"]},
        )

    op.alter_column("articles", "workspace_id", nullable=False)
    op.create_foreign_key(
        "fk_articles_workspace_id",
        "articles",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_articles_client_id",
        "articles",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_articles_assignee_id",
        "articles",
        "users",
        ["assignee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_articles_workspace_id", "articles", ["workspace_id"])
    op.create_index("ix_articles_client_id", "articles", ["client_id"])
    op.create_index("ix_articles_assignee_id", "articles", ["assignee_id"])
    op.create_check_constraint(
        "ck_articles_status",
        "articles",
        "status IN ('setup', 'waiting_for_client', 'interview_in_progress', "
        "'ready_to_draft', 'drafting', 'in_review', 'ready_to_publish', 'published')",
    )
    op.create_check_constraint(
        "ck_articles_content_type",
        "articles",
        "content_type IN ('blog_post', 'thought_leadership', 'case_study', 'guide', "
        "'landing_page')",
    )
    op.create_check_constraint(
        "ck_articles_target_length",
        "articles",
        "target_length IN ('short', 'standard', 'long')",
    )
    op.create_check_constraint(
        "ck_articles_interview_method",
        "articles",
        "interview_method IN ('client', 'self', 'notes')",
    )
    op.create_check_constraint(
        "ck_articles_published_state",
        "articles",
        "(status = 'published' AND published_at IS NOT NULL) OR "
        "(status <> 'published' AND published_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_articles_published_state", "articles", type_="check")
    op.drop_constraint("ck_articles_interview_method", "articles", type_="check")
    op.drop_constraint("ck_articles_target_length", "articles", type_="check")
    op.drop_constraint("ck_articles_content_type", "articles", type_="check")
    op.drop_constraint("ck_articles_status", "articles", type_="check")
    op.drop_index("ix_articles_assignee_id", table_name="articles")
    op.drop_index("ix_articles_client_id", table_name="articles")
    op.drop_index("ix_articles_workspace_id", table_name="articles")
    op.drop_constraint("fk_articles_assignee_id", "articles", type_="foreignkey")
    op.drop_constraint("fk_articles_client_id", "articles", type_="foreignkey")
    op.drop_constraint("fk_articles_workspace_id", "articles", type_="foreignkey")
    for column in (
        "published_at",
        "draft_readiness",
        "seo_keyword",
        "tone",
        "call_to_action",
        "key_message",
        "main_angle",
        "interview_instructions",
        "interviewee_name",
        "interview_method",
        "target_length",
        "due_date",
        "content_type",
        "status",
        "assignee_id",
        "client_id",
        "workspace_id",
    ):
        op.drop_column("articles", column)
    op.drop_index("uq_client_brand_profiles_client_id", table_name="client_brand_profiles")
    op.drop_table("client_brand_profiles")
    op.drop_index("uq_clients_workspace_name_ci", table_name="clients")
    op.drop_index("ix_clients_workspace_id", table_name="clients")
    op.drop_table("clients")
    op.drop_index("uq_workspace_members_default_user", table_name="workspace_members")
    op.drop_index("ix_workspace_members_user_id", table_name="workspace_members")
    op.drop_index("ix_workspace_members_workspace_id", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
