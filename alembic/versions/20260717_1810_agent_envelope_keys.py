"""Add pinned target-agent envelope key material.

Revision ID: 20260717_1810
Revises: 20260717_0715
Create Date: 2026-07-17 18:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_1810"
down_revision: str | None = "20260717_0715"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cluster_registrations",
        sa.Column("agent_envelope_public_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "cluster_registrations",
        sa.Column("agent_envelope_private_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cluster_registrations", "agent_envelope_private_key_encrypted")
    op.drop_column("cluster_registrations", "agent_envelope_public_key")
