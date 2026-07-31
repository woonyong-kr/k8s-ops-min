"""Add durable alert evaluation state and event ledger.

Revision ID: 20260715_0215
Revises: 20260715_0145
Create Date: 2026-07-15 02:15:00

Resolved events remain in the ledger after their source rule is deleted. Only
the evaluator's per-target state is owned by the rule lifecycle.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260715_0215"
down_revision: str | None = "20260715_0145"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_name", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=False),
        sa.Column("subject", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("observed_value", sa.Float(precision=53), nullable=False),
        sa.Column("threshold", sa.Float(precision=53), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("incident_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_alert_events_workspace_fired",
        "alert_events",
        ["workspace_id", "fired_at"],
        unique=False,
    )
    op.create_index(
        "ix_alert_events_workspace_status",
        "alert_events",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_alert_events_active_subject",
        "alert_events",
        ["rule_id", "subject_key"],
        unique=True,
        postgresql_where=sa.text("status in ('firing', 'acked')"),
    )

    op.create_table(
        "alert_rule_target_states",
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("subject", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("condition_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_event_id", sa.Text(), nullable=True),
        sa.Column("last_observed_value", sa.Float(precision=53), nullable=False),
        sa.Column("last_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["alert_rules.rule_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rule_id", "subject_key"),
    )
    op.create_index(
        "ix_alert_rule_target_states_workspace_rule",
        "alert_rule_target_states",
        ["workspace_id", "rule_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_rule_target_states_workspace_rule",
        table_name="alert_rule_target_states",
    )
    op.drop_table("alert_rule_target_states")
    op.drop_index("uq_alert_events_active_subject", table_name="alert_events")
    op.drop_index("ix_alert_events_workspace_status", table_name="alert_events")
    op.drop_index("ix_alert_events_workspace_fired", table_name="alert_events")
    op.drop_table("alert_events")
