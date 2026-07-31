"""outbox all-source relay claim index.

Revision ID: 20260708_0620
Revises: 20260708_0610
Create Date: 2026-07-08 06:20:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260708_0620"
down_revision: str | None = "20260708_0610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_outbox_claim_all_sources
                ON outbox (sent_at, leased_until, id)
                WHERE sent_at IS NULL
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_outbox_claim_all_sources"))
