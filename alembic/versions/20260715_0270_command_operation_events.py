"""Create durable, replayable command operation event storage.

Revision ID: 20260715_0270
Revises: 20260715_0260
Create Date: 2026-07-15 20:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260715_0270"
down_revision: str | None = "20260715_0260"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "command_operation_event_cursors",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("terminal_sequence", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("workspace_id", "command_id"),
    )
    op.create_table(
        "command_operation_events",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("workspace_id", "command_id", "sequence"),
    )
    op.create_index(
        "ix_command_operation_events_replay",
        "command_operation_events",
        ["workspace_id", "command_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_command_operation_events_replay", table_name="command_operation_events")
    op.drop_table("command_operation_events")
    op.drop_table("command_operation_event_cursors")
