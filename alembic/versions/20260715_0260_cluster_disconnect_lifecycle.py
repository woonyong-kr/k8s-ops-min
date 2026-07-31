"""Separate disconnected registrations from expired install commands.

Revision ID: 20260715_0260
Revises: 20260715_0250
Create Date: 2026-07-15 13:55:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_0260"
down_revision: str | None = "20260715_0250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ux_cluster_registrations_workspace_active_name"


def upgrade() -> None:
    # The previous partial unique index still treats ``disconnected`` as an
    # active name.  Drop it before converting legacy rows; otherwise a stale
    # disconnected registration can collide with the live registration that
    # legitimately reused the same name.
    op.drop_index(INDEX_NAME, table_name="cluster_registrations")
    # 기존 unregister 경로는 토큰을 폐기하면서 install_expired를 기록했다.
    # 토큰이 남은 실제 설치 만료 행과 안전하게 구분해 과거 해제 카드도 숨긴다.
    op.execute(
        sa.text(
            """
            update cluster_registrations
            set status = 'disconnected', updated_at = now()
            where status = 'install_expired'
              and agent_token_hash is null
            """
        )
    )
    op.create_index(
        INDEX_NAME,
        "cluster_registrations",
        ["workspace_id", sa.text("lower(btrim(name))")],
        unique=True,
        postgresql_where=sa.text("status not in ('install_expired', 'disconnected')"),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            update cluster_registrations
            set status = 'install_expired', updated_at = now()
            where status = 'disconnected'
            """
        )
    )
    op.drop_index(INDEX_NAME, table_name="cluster_registrations")
    op.create_index(
        INDEX_NAME,
        "cluster_registrations",
        ["workspace_id", sa.text("lower(btrim(name))")],
        unique=True,
        postgresql_where=sa.text("status <> 'install_expired'"),
    )
