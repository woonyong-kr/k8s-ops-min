"""Index exact Helm ownership metadata lookups.

Revision ID: 20260716_0800
Revises: 20260716_0700
Create Date: 2026-07-16 20:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0800"
down_revision: str | None = "20260716_0700"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_inventory_resources_helm_ownership"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX {INDEX_NAME}
        ON cluster_inventory_resources (
            workspace_id,
            cluster_id,
            namespace,
            (annotations ->> 'meta.helm.sh/release-name'),
            (annotations ->> 'meta.helm.sh/release-namespace')
        )
        WHERE deleted_at IS NULL
          AND labels ->> 'app.kubernetes.io/managed-by' = 'Helm'
          AND annotations ? 'meta.helm.sh/release-name'
          AND annotations ? 'meta.helm.sh/release-namespace'
        """
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="cluster_inventory_resources")
