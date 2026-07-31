"""RCA Issues category projection and bounded queue index.

Revision ID: 20260717_0715
Revises: 20260716_1100
Create Date: 2026-07-17 07:15:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_0715"
down_revision: str | None = "20260716_1100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_rca_timeline_issue_category"


def upgrade() -> None:
    op.add_column("rca_timeline", sa.Column("category", sa.Text(), nullable=True))
    op.add_column(
        "rca_timeline",
        sa.Column(
            "category_complete",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
                "ON rca_timeline (workspace_id, category, updated_at DESC, id DESC) "
                "WHERE category_complete IS TRUE"
            )
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
    op.drop_column("rca_timeline", "category_complete")
    op.drop_column("rca_timeline", "category")
