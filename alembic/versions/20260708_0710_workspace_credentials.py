"""workspace encrypted credentials.

Revision ID: 20260708_0710
Revises: 20260708_0620
Create Date: 2026-07-08 07:10:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260708_0710"
down_revision: str | None = "20260708_0620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_credentials",
        sa.Column("credential_id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Text(), sa.ForeignKey("workspaces.workspace_id"), nullable=False
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("workspace_id", "provider", "scope"),
    )


def downgrade() -> None:
    op.drop_table("workspace_credentials")
