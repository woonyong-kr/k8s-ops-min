"""Persist per-resource GitOps approval snapshots.

Revision ID: 20260724_0300
Revises: 20260724_0200
Create Date: 2026-07-24 14:05:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260724_0300"
down_revision: str | None = "20260724_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workload_changes",
        sa.Column(
            "diff_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "gitops_approved_resource_snapshots",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("binding_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("resource_name", sa.Text(), nullable=False),
        sa.Column("workflow_run_id", sa.Text(), nullable=False),
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("artifact_digest", sa.Text(), nullable=False),
        sa.Column(
            "managed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "binding_id",
            "cluster_id",
            "namespace",
            "resource_kind",
            "resource_name",
        ),
    )
    op.create_index(
        "ix_gitops_approved_snapshots_workflow",
        "gitops_approved_resource_snapshots",
        ["workspace_id", "workflow_run_id", "command_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gitops_approved_snapshots_workflow",
        table_name="gitops_approved_resource_snapshots",
    )
    op.drop_table("gitops_approved_resource_snapshots")
    op.drop_column("workload_changes", "diff_details")
