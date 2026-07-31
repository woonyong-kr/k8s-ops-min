"""Add external-event and operator lifecycle fields to the alert ledger.

Revision ID: 20260715_0230
Revises: 20260715_0215
Create Date: 2026-07-15 02:30:00

Alertmanager events do not have an Opsia rule or threshold. Acknowledgement and
incident promotion remain durable operator actions on both event sources.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_0230"
down_revision: str | None = "20260715_0215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("alert_events", "rule_id", existing_type=sa.Text(), nullable=True)
    op.alter_column("alert_events", "rule_name", existing_type=sa.Text(), nullable=True)
    op.alter_column(
        "alert_events",
        "observed_value",
        existing_type=sa.Float(precision=53),
        nullable=True,
    )
    op.alter_column(
        "alert_events",
        "threshold",
        existing_type=sa.Float(precision=53),
        nullable=True,
    )
    op.add_column(
        "alert_events",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("alert_events", sa.Column("acknowledged_by", sa.Text(), nullable=True))
    op.add_column(
        "alert_events",
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("alert_events", sa.Column("promoted_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_events", "promoted_by")
    op.drop_column("alert_events", "promoted_at")
    op.drop_column("alert_events", "acknowledged_by")
    op.drop_column("alert_events", "acknowledged_at")
    op.alter_column(
        "alert_events",
        "threshold",
        existing_type=sa.Float(precision=53),
        nullable=False,
    )
    op.alter_column(
        "alert_events",
        "observed_value",
        existing_type=sa.Float(precision=53),
        nullable=False,
    )
    op.alter_column("alert_events", "rule_name", existing_type=sa.Text(), nullable=False)
    op.alter_column("alert_events", "rule_id", existing_type=sa.Text(), nullable=False)
