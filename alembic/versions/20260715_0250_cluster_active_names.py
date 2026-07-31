"""Prevent duplicate active cluster display names inside a workspace.

Revision ID: 20260715_0250
Revises: 20260715_0240
Create Date: 2026-07-15 02:50:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_0250"
down_revision: str | None = "20260715_0240"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ux_cluster_registrations_workspace_active_name"


def upgrade() -> None:
    # 기존 중복이 있으면 관리 클러스터를 우선 보존하고, 그 외에는 가장 최근 등록만
    # 활성으로 남긴다. 만료 처리된 행은 감사 이력으로 보존되며 agent 토큰은 폐기한다.
    op.execute(
        sa.text(
            """
            with ranked as (
                select id,
                       row_number() over (
                           partition by workspace_id, lower(btrim(name))
                           order by
                               case when settings->>'cluster_role' = 'management' then 0 else 1 end,
                               updated_at desc,
                               id desc
                       ) as duplicate_rank
                from cluster_registrations
                where status <> 'install_expired'
            )
            update cluster_registrations as registration
            set status = 'install_expired',
                agent_token_hash = null,
                updated_at = now()
            from ranked
            where registration.id = ranked.id
              and ranked.duplicate_rank > 1
            """
        )
    )
    op.create_index(
        INDEX_NAME,
        "cluster_registrations",
        ["workspace_id", sa.text("lower(btrim(name))")],
        unique=True,
        postgresql_where=sa.text("status <> 'install_expired'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="cluster_registrations")
