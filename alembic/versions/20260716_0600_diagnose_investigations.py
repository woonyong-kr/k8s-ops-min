"""Create durable resource investigation runs, events, and disclosure consent.

Revision ID: 20260716_0600
Revises: 20260716_0500
Create Date: 2026-07-16 16:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260716_0600"
down_revision: str | None = "20260716_0500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnose_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("deduplication_key", sa.Text(), nullable=False),
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("agent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ux_diagnose_runs_active_deduplication",
        "diagnose_runs",
        ["workspace_id", "deduplication_key"],
        unique=True,
        postgresql_where=sa.text("active IS TRUE"),
    )
    op.create_index(
        "ix_diagnose_runs_history",
        "diagnose_runs",
        ["workspace_id", "requested_by", "updated_at"],
    )
    op.create_table(
        "diagnose_event_cursors",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["diagnose_runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "diagnose_events",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["diagnose_runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "sequence"),
    )
    op.create_index(
        "ix_diagnose_events_replay",
        "diagnose_events",
        ["workspace_id", "run_id", "sequence"],
    )
    op.create_table(
        "diagnose_consents",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("disclosure_revision", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "requested_by",
            "agent_id",
            "disclosure_revision",
            "surface",
        ),
    )


def downgrade() -> None:
    op.drop_table("diagnose_consents")
    op.drop_index("ix_diagnose_events_replay", table_name="diagnose_events")
    op.drop_table("diagnose_events")
    op.drop_table("diagnose_event_cursors")
    op.drop_index("ix_diagnose_runs_history", table_name="diagnose_runs")
    op.drop_index(
        "ux_diagnose_runs_active_deduplication",
        table_name="diagnose_runs",
    )
    op.drop_table("diagnose_runs")
