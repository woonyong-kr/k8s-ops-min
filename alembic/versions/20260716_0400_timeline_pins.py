"""Create persistent, revisioned Timeline pin storage.

Revision ID: 20260716_0400
Revises: 20260715_0300
Create Date: 2026-07-16 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260716_0400"
down_revision: str | None = "20260715_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timeline_pin_sets",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
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
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_table(
        "timeline_pins",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pin_id", sa.Text(), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=False),
        sa.Column("subject", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "pin_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "subject_key",
            name="uq_timeline_pins_owner_subject",
        ),
    )
    op.create_index(
        "ix_timeline_pins_owner",
        "timeline_pins",
        ["workspace_id", "user_id", "created_at", "pin_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_timeline_pins_owner", table_name="timeline_pins")
    op.drop_table("timeline_pins")
    op.drop_table("timeline_pin_sets")
