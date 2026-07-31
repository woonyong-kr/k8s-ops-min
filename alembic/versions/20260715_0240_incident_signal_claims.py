"""Add durable idempotency claims for polled incident signals.

Revision ID: 20260715_0240
Revises: 20260715_0230
Create Date: 2026-07-15 02:40:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260715_0240"
down_revision: str | None = "20260715_0230"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_signal_claims",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("signal_key", sa.Text(), nullable=False),
        sa.Column("first_correlation_id", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "cluster_id",
            "signal_key",
            name="uq_incident_signal_claim_identity",
        ),
    )


def downgrade() -> None:
    op.drop_table("incident_signal_claims")
