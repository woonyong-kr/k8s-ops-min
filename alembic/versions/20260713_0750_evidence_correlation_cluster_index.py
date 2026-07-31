"""evidence correlation cluster 권위 조회 인덱스.

Revision ID: 20260713_0750
Revises: 20260713_0655
Create Date: 2026-07-13 07:50:00

CONCURRENTLY 실패 시 INVALID 인덱스가 남을 수 있으므로 재시도 전에 제거한다.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_0750"
down_revision: str | None = "20260713_0655"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_evidence_windows_workspace_correlation_cluster"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
        op.execute(
            sa.text(
                f"""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                    {INDEX_NAME}
                ON evidence_windows (workspace_id, correlation_id, cluster_id)
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
