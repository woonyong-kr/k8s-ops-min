"""Separate recurring RCA incidents into durable occurrence cycles.

Revision ID: 20260725_0400
Revises: 20260724_0300
Create Date: 2026-07-25 04:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260725_0400"
down_revision: str | None = "20260724_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rca_timeline",
        sa.Column("incident_occurrence_id", sa.Text(), nullable=True),
    )
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rca_timeline_occurrence
                ON rca_timeline (
                    workspace_id,
                    incident_occurrence_id,
                    updated_at
                )
                WHERE incident_occurrence_id IS NOT NULL
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "DROP INDEX CONCURRENTLY IF EXISTS ix_rca_timeline_occurrence"
            )
        )
    op.drop_column("rca_timeline", "incident_occurrence_id")
