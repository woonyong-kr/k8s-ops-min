"""event outbox와 audit log의 workspace 귀속.

Revision ID: 20260713_0655
Revises: 20260713_0140
Create Date: 2026-07-13 06:55:00

기존 행은 backfill하지 않고 NULL로 유지한다. CONCURRENTLY 빌드 실패 시 INVALID
인덱스가 남을 수 있으므로 재시도 전에 운영자가 상태를 확인하고 제거해야 한다.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_0655"
down_revision: str | None = "20260713_0140"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_audit_log_workspace_id_correlation_id_created_at"


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE outbox ADD COLUMN IF NOT EXISTS workspace_id TEXT"))
    op.execute(sa.text("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS workspace_id TEXT"))
    with op.get_context().autocommit_block():
        # 이전 CONCURRENTLY 실패가 남긴 INVALID 인덱스도 재시도에서 제거한다.
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
        op.execute(
            sa.text(
                f"""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                    {INDEX_NAME}
                ON audit_log (workspace_id, correlation_id, created_at)
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"))
    op.execute(sa.text("ALTER TABLE audit_log DROP COLUMN IF EXISTS workspace_id"))
    op.execute(sa.text("ALTER TABLE outbox DROP COLUMN IF EXISTS workspace_id"))
