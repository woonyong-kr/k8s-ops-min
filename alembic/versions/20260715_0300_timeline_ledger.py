"""Create immutable timeline evidence storage and replay cursors.

Revision ID: 20260715_0300
Revises: 20260715_0270
Create Date: 2026-07-15 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260715_0300"
down_revision: str | None = "20260715_0270"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timeline_event_cursors",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("retained_from_sequence", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "timeline_events",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=False),
        sa.Column("activity", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("freshness", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resource", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("owner", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("workspace_id", "sequence"),
        sa.UniqueConstraint("workspace_id", "source_key", name="uq_timeline_events_source_key"),
    )
    op.create_index(
        "ix_timeline_events_replay",
        "timeline_events",
        ["workspace_id", "sequence"],
    )
    op.create_index(
        "ix_timeline_events_scope_time",
        "timeline_events",
        ["workspace_id", "cluster_id", "namespace", "occurred_at", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_timeline_events_scope_time", table_name="timeline_events")
    op.drop_index("ix_timeline_events_replay", table_name="timeline_events")
    op.drop_table("timeline_events")
    op.drop_table("timeline_event_cursors")
