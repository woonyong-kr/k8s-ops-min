"""Add Opsia-owned alert rules.

Revision ID: 20260715_0145
Revises: 20260714_0200
Create Date: 2026-07-15 01:45:00

These rules are stored in the Opsia database. They are not Kubernetes
``PrometheusRule`` resources and never enter the GitOps writer path.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260715_0145"
down_revision: str | None = "20260714_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("comparator", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Float(precision=53), nullable=False),
        sa.Column("for_seconds", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("channels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.PrimaryKeyConstraint("rule_id"),
    )
    op.create_index(
        "ix_alert_rules_workspace_enabled",
        "alert_rules",
        ["workspace_id", "enabled"],
        unique=False,
    )
    op.create_index(
        "ix_alert_rules_workspace_name",
        "alert_rules",
        ["workspace_id", "name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alert_rules_workspace_name", table_name="alert_rules")
    op.drop_index("ix_alert_rules_workspace_enabled", table_name="alert_rules")
    op.drop_table("alert_rules")
