"""Add create-all metric tables to the versioned schema.

Revision ID: 20260714_0200
Revises: 20260713_2350
Create Date: 2026-07-14 02:00:00

Both tables already exist in legacy ``Database.init()`` databases. This
additive revision extends the versioned schema; the production migration
runner separately rejects unversioned databases.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_0200"
down_revision: str | None = "20260713_2350"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "event_processing",
        sa.Column("processing_duration_ms", sa.Integer(), nullable=True),
    )
    op.create_table(
        "event_consumer_metrics",
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("stream", sa.Text(), nullable=False),
        sa.Column("pending_events", sa.BigInteger(), nullable=False),
        sa.Column("ack_pending_events", sa.BigInteger(), nullable=False),
        sa.Column("redelivered_events", sa.BigInteger(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("consumer", "subject"),
    )
    op.create_table(
        "ai_llm_invocation_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("causation_id", sa.Text(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_llm_invocation_correlation_created",
        "ai_llm_invocation_metrics",
        ["correlation_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("correlation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_llm_invocation_correlation_created",
        table_name="ai_llm_invocation_metrics",
    )
    op.drop_table("ai_llm_invocation_metrics")
    op.drop_table("event_consumer_metrics")
    op.drop_column("event_processing", "processing_duration_ms")
