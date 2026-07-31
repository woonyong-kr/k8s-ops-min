"""Persist the exact standard SLI series identity on alert events.

Revision ID: 20260724_0200
Revises: 20260723_0100
Create Date: 2026-07-24 11:05:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260724_0200"
down_revision: str | None = "20260723_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alert_events",
        sa.Column("series_identity", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alert_events", "series_identity")
