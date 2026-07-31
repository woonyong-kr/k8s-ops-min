"""Add subject_key so one asset can hold several findings per check.

기존 유일 제약은 (dag_run_id, check_name, asset_id) 였다. 한 검사가 한 자산에서
위반을 여러 건 찾으면 — 03 은 필드마다, 02 는 누락 필드마다, 08 은 리소스마다 —
ON CONFLICT 가 두 번째부터 앞의 것을 덮어써서 자산당 1건만 남았다.

create_all 은 IF NOT EXISTS 라서 이미 만들어진 테이블에 컬럼을 붙이지 않는다.
운영 DB 를 가진 쪽에는 이 리비전이 필요하다.

Revision ID: 20260731_0100
Revises: 20260725_0400
Create Date: 2026-07-31 01:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_0100"
down_revision: str | None = "20260725_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "catalog_quality_results"
CONSTRAINT = "uq_catalog_quality_results"


def upgrade() -> None:
    # 기존 행에는 '-' 를 채운다. NULL 을 두면 유일 제약이 서로 다른 값으로 취급해
    # ON CONFLICT 가 발화하지 않고 재실행마다 행이 늘어난다.
    op.add_column(
        TABLE,
        sa.Column("subject_key", sa.Text(), nullable=False, server_default="-"),
    )
    op.drop_constraint(CONSTRAINT, TABLE, type_="unique")
    op.create_unique_constraint(
        CONSTRAINT, TABLE, ["dag_run_id", "check_name", "asset_id", "subject_key"]
    )


def downgrade() -> None:
    # 되돌리면 자산당 여러 건이 유일 제약을 위반한다. 가장 최근 것만 남긴다.
    op.execute(
        f"""
        DELETE FROM {TABLE} a USING {TABLE} b
        WHERE a.ctid < b.ctid
          AND a.dag_run_id = b.dag_run_id
          AND a.check_name = b.check_name
          AND a.asset_id   = b.asset_id
        """
    )
    op.drop_constraint(CONSTRAINT, TABLE, type_="unique")
    op.create_unique_constraint(CONSTRAINT, TABLE, ["dag_run_id", "check_name", "asset_id"])
    op.drop_column(TABLE, "subject_key")
