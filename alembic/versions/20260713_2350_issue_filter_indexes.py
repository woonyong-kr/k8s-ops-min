"""Issues filter concurrent indexes.

Revision ID: 20260713_2350
Revises: 20260713_2340
Create Date: 2026-07-13 23:50:00

컬럼 transaction과 분리되어 CONCURRENTLY 빌드 실패 후 이 revision만 안전하게 재실행할
수 있다. 실패 시 남을 수 있는 INVALID 인덱스는 각 CREATE 전에 같은 이름으로 제거한다.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_2350"
down_revision: str | None = "20260713_2340"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_DDLS = {
    "ix_rca_timeline_issue_page": ("ON rca_timeline (workspace_id, updated_at DESC, id DESC)"),
    "ix_rca_timeline_issue_identity_latest": (
        "ON rca_timeline (workspace_id, cluster_id, incident_id, updated_at DESC, id DESC)"
    ),
    "ix_rca_timeline_issue_severity": (
        "ON rca_timeline (workspace_id, severity, updated_at DESC, id DESC)"
    ),
    "ix_rca_timeline_issue_environment": (
        "ON rca_timeline (workspace_id, environment, updated_at DESC, id DESC)"
    ),
    "ix_rca_timeline_issue_applications": "ON rca_timeline USING gin (application_ids)",
    "ix_rca_timeline_issue_labels": "ON rca_timeline USING gin (labels)",
}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, definition in INDEX_DDLS.items():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
            op.execute(sa.text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} {definition}"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(tuple(INDEX_DDLS)):
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
