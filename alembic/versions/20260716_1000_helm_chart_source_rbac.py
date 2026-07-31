"""Seed Helm chart-source role permissions from the catalog policy.

Revision ID: 20260716_1000
Revises: 20260716_0900
Create Date: 2026-07-16 22:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_1000"
down_revision: str | None = "20260716_0900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permissions (
            organization_id,
            resource_type,
            role,
            permission,
            status,
            created_at,
            updated_at
        )
        SELECT
            organization_id,
            'helm_chart_source',
            role,
            permission,
            status,
            now(),
            now()
        FROM role_permissions
        WHERE resource_type = 'catalog_item'
        ON CONFLICT (organization_id, resource_type, role, permission)
        DO UPDATE SET
            status = EXCLUDED.status,
            updated_at = now()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE resource_type = 'helm_chart_source'
        """
    )
