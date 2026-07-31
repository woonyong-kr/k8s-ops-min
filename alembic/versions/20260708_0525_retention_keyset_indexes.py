"""retention/keyset 조회 인덱스.

Revision ID: 20260708_0525
Revises: 20260708_0435
Create Date: 2026-07-08 05:25:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260708_0525"
down_revision: str | None = "20260708_0435"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_outbox_sent_at
                ON outbox (sent_at)
                WHERE sent_at IS NOT NULL
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_created_at
                ON events (created_at)
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_log_created_at
                ON audit_log (created_at)
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_evidence_workspace_correlation_created
                ON evidence (workspace_id, correlation_id, created_at, id)
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_evidence_workspace_created_id
                ON evidence (workspace_id, created_at, id)
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rca_reports_workspace_correlation_created
                ON rca_reports (workspace_id, correlation_id, created_at, id)
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rca_reports_workspace_created_id
                ON rca_reports (workspace_id, created_at, id)
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in (
            "ix_rca_reports_workspace_created_id",
            "ix_rca_reports_workspace_correlation_created",
            "ix_evidence_workspace_created_id",
            "ix_evidence_workspace_correlation_created",
            "ix_audit_log_created_at",
            "ix_events_created_at",
            "ix_outbox_sent_at",
        ):
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
