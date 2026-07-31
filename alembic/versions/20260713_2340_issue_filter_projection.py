"""Issues filter event-time projection columns.

Revision ID: 20260713_2340
Revises: 20260713_2215
Create Date: 2026-07-13 23:40:00

기존 timeline은 backfill하지 않아 새 축의 완전성을 과장하지 않는다. 컬럼 transaction과
CONCURRENTLY 인덱스를 별도 revision으로 분리해 인덱스 실패 뒤 재실행 가능성을 보장한다.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260713_2340"
down_revision: str | None = "20260713_2215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rca_timeline", sa.Column("severity", sa.Text(), nullable=True))
    op.add_column("rca_timeline", sa.Column("environment", sa.Text(), nullable=True))
    op.add_column(
        "rca_timeline",
        sa.Column("application_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "rca_timeline",
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    for column_name in (
        "severity_complete",
        "environment_complete",
        "application_ids_complete",
        "labels_complete",
    ):
        op.add_column(
            "rca_timeline",
            sa.Column(
                column_name,
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for column_name in (
        "labels_complete",
        "application_ids_complete",
        "environment_complete",
        "severity_complete",
        "labels",
        "application_ids",
        "environment",
        "severity",
    ):
        op.drop_column("rca_timeline", column_name)
