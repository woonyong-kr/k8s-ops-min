"""rca_timeline fleet open incident 집계 인덱스.

Revision ID: 20260708_0405
Revises: None
Create Date: 2026-07-08 04:05:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260708_0405"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rca_timeline_fleet_open_logical
                ON rca_timeline (
                  workspace_id,
                  cluster_id,
                  (
                    CASE
                      WHEN NULLIF(payload #>> '{incident,namespace}', '') IS NOT NULL
                        OR NULLIF(payload #>> '{incident,resource_kind}', '') IS NOT NULL
                        OR NULLIF(payload #>> '{incident,resource_name}', '') IS NOT NULL
                        OR NULLIF(payload #>> '{incident,symptom}', '') IS NOT NULL
                      THEN cluster_id || '|' ||
                           COALESCE(NULLIF(payload #>> '{incident,namespace}', ''), 'unknown') || '|' ||
                           COALESCE(NULLIF(payload #>> '{incident,resource_kind}', ''), 'unknown') || '|' ||
                           COALESCE(NULLIF(payload #>> '{incident,resource_name}', ''), 'unknown') || '|' ||
                           COALESCE(NULLIF(payload #>> '{incident,symptom}', ''), 'unknown')
                      ELSE COALESCE(incident_id, correlation_id, id::text)
                    END
                  )
                )
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
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_rca_timeline_fleet_open_logical"))
