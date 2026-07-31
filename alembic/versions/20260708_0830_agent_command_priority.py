"""agent command priority queue.

Revision ID: 20260708_0830
Revises: 20260708_0710
Create Date: 2026-07-08 08:30:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260708_0830"
down_revision: str | None = "20260708_0710"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE agent_commands ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 100"
        )
    )
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_agent_commands_available_priority
                ON agent_commands (workspace_id, cluster_id, status, priority DESC, created_at)
                WHERE status IN ('queued', 'leased', 'running')
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_agent_commands_available_priority")
        )
    op.drop_column("agent_commands", "priority")
