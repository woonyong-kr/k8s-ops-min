"""audit log causation timeline.

Revision ID: 20260713_0140
Revises: 20260710_0900
Create Date: 2026-07-13 01:40:00

주의: CONCURRENTLY 빌드 실패 시 INVALID 인덱스가 남을 수 있어 재시도 전에 수동 정리가 필요하다.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260713_0140"
down_revision: str | None = "20260710_0900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("causation_id", sa.Text(), nullable=True))
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                    ix_audit_log_correlation_id_created_at
                ON audit_log (correlation_id, created_at)
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                DROP INDEX CONCURRENTLY IF EXISTS
                    ix_audit_log_correlation_id_created_at
                """
            )
        )
    op.drop_column("audit_log", "causation_id")
