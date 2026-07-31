"""alert channel validation status.

Revision ID: 20260710_0900
Revises: 20260706_0001
Create Date: 2026-07-10 09:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260710_0900"
down_revision: str | None = "20260706_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE alert_channels ADD COLUMN IF NOT EXISTS last_tested_at TIMESTAMPTZ")
    )
    op.execute(sa.text("ALTER TABLE alert_channels ADD COLUMN IF NOT EXISTS last_test_status TEXT"))
    op.execute(sa.text("ALTER TABLE alert_channels ADD COLUMN IF NOT EXISTS last_test_detail TEXT"))
    op.execute(
        sa.text("ALTER TABLE alert_channels ADD COLUMN IF NOT EXISTS last_test_status_code INTEGER")
    )


def downgrade() -> None:
    op.drop_column("alert_channels", "last_test_status_code")
    op.drop_column("alert_channels", "last_test_detail")
    op.drop_column("alert_channels", "last_test_status")
    op.drop_column("alert_channels", "last_tested_at")
