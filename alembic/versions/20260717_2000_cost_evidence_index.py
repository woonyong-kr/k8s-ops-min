"""Add the bounded Cost evidence lookup index.

Revision ID: 20260717_2000
Revises: 20260717_1915
Create Date: 2026-07-17 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_2000"
down_revision: str | None = "20260717_1915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_evidence_windows_workspace_cluster_updated"


def _preflight_offline_invalid_index() -> None:
    if not op.get_context().as_sql:
        return
    op.execute(
        "DO $opsia$ "
        "BEGIN "
        "IF EXISTS ("
        "SELECT 1 FROM pg_catalog.pg_class AS relation "
        "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
        "JOIN pg_catalog.pg_index AS index_state ON index_state.indexrelid = relation.oid "
        "WHERE namespace.nspname = current_schema() "
        f"AND relation.relname = '{INDEX_NAME}' AND index_state.indisvalid IS FALSE"
        ") THEN RAISE EXCEPTION 'invalid concurrent index remnant: %; run online migration', "
        f"'{INDEX_NAME}'; END IF; END $opsia$"
    )


def _drop_invalid_index() -> None:
    if op.get_context().as_sql:
        return
    invalid = bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS("
                "SELECT 1 FROM pg_catalog.pg_class AS relation "
                "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_index AS index_state ON index_state.indexrelid = relation.oid "
                "WHERE namespace.nspname = current_schema() "
                "AND relation.relname = :index_name AND index_state.indisvalid IS FALSE)"
            ),
            {"index_name": INDEX_NAME},
        )
        .scalar_one()
    )
    if invalid:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _preflight_offline_invalid_index()
            _drop_invalid_index()
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{INDEX_NAME} ON evidence_windows "
                "(workspace_id, cluster_id, updated_at)"
            )
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        finally:
            op.execute("RESET lock_timeout")
