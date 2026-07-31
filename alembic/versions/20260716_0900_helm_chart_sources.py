"""Workspace-scoped Helm chart source registry.

Revision ID: 20260716_0900
Revises: 20260716_0800
Create Date: 2026-07-16 21:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260716_0900"
down_revision: str | None = "20260716_0800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "helm_chart_sources",
        sa.Column("source_id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Text(),
            sa.ForeignKey("workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("canonical_ref", sa.Text(), nullable=False),
        sa.Column("credential_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "access_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "provider IN ('repository', 'oci')",
            name="ck_helm_chart_sources_provider",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_helm_chart_sources_status",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "canonical_ref",
            name="uq_helm_chart_sources_workspace_provider_ref",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_helm_chart_sources_workspace_name",
        ),
    )
    op.create_index(
        "ix_helm_chart_sources_workspace_updated",
        "helm_chart_sources",
        ["workspace_id", "updated_at", "source_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_helm_chart_sources_workspace_updated",
        table_name="helm_chart_sources",
    )
    op.drop_table("helm_chart_sources")
