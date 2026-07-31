"""rca_timeline incident projection 컬럼.

Revision ID: 20260708_0435
Revises: 20260708_0405
Create Date: 2026-07-08 04:35:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260708_0435"
down_revision: str | None = "20260708_0405"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rca_timeline", sa.Column("incident_namespace", sa.Text(), nullable=True))
    op.add_column("rca_timeline", sa.Column("incident_resource_kind", sa.Text(), nullable=True))
    op.add_column("rca_timeline", sa.Column("incident_resource_name", sa.Text(), nullable=True))
    op.add_column("rca_timeline", sa.Column("incident_symptom", sa.Text(), nullable=True))
    op.add_column("rca_timeline", sa.Column("incident_logical_key", sa.Text(), nullable=True))
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rca_timeline_fleet_open_projection
                ON rca_timeline (workspace_id, cluster_id, incident_logical_key)
                WHERE incident_id IS NOT NULL
                  AND cluster_id IS NOT NULL
                  AND status NOT IN ('command_completed', 'command_rejected', 'pr_created', 'pr_failed')
                  AND (
                    current_subject <> 'incident.detected'
                    OR CAST((payload ->> 'detected') AS boolean) IS TRUE
                  )
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_rca_timeline_fleet_open_projection")
        )
    op.drop_column("rca_timeline", "incident_logical_key")
    op.drop_column("rca_timeline", "incident_symptom")
    op.drop_column("rca_timeline", "incident_resource_name")
    op.drop_column("rca_timeline", "incident_resource_kind")
    op.drop_column("rca_timeline", "incident_namespace")
