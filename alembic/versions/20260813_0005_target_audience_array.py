"""Store article target audiences as arrays.

Revision ID: 20260813_0005
Revises: 20260812_0004
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260813_0005"
down_revision: str | None = "20260812_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "articles",
        "target_audience",
        existing_type=sa.String(length=500),
        type_=postgresql.ARRAY(sa.String(length=500)),
        existing_nullable=False,
        postgresql_using="ARRAY[target_audience]::varchar(500)[]",
    )
    op.create_check_constraint(
        "ck_articles_target_audience_count",
        "articles",
        "cardinality(target_audience) BETWEEN 1 AND 10",
    )
    op.create_check_constraint(
        "ck_articles_target_audience_no_nulls",
        "articles",
        "array_position(target_audience, NULL) IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_articles_target_audience_no_nulls", "articles", type_="check")
    op.drop_constraint("ck_articles_target_audience_count", "articles", type_="check")
    op.alter_column(
        "articles",
        "target_audience",
        existing_type=postgresql.ARRAY(sa.String(length=500)),
        type_=sa.String(length=500),
        existing_nullable=False,
        postgresql_using="target_audience[1]::varchar(500)",
    )
