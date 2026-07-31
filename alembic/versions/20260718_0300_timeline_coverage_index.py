"""Add the ordered Timeline coverage proof partial index.

Revision ID: 20260718_0300
Revises: 20260718_0200
Create Date: 2026-07-18 09:50:00
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260718_0300"
down_revision: str | None = "20260718_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_inventory_snapshots_timeline_coverage"
INDEX_PREDICATE = (
    "status != 'ignored_stale' AND summary['summary']['kubernetes_event_capture'] IS NOT NULL"
)
LOCK_TIMEOUT = "5s"
CREATE_RETRY_DELAYS = (2.0, 4.0, 8.0)


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


def _is_lock_timeout(error: sa.exc.OperationalError) -> bool:
    original = getattr(error, "orig", None)
    return getattr(original, "sqlstate", None) == "55P03" or "lock timeout" in str(error).casefold()


def _create_index_with_bounded_retry() -> None:
    statement = (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        f"{INDEX_NAME} ON cluster_inventory_snapshots "
        "(workspace_id, cluster_id, collected_at, created_at, snapshot_id) "
        f"WHERE {INDEX_PREDICATE}"
    )
    if op.get_context().as_sql:
        op.execute(statement)
        return

    for attempt in range(len(CREATE_RETRY_DELAYS) + 1):
        _drop_invalid_index()
        try:
            op.execute(statement)
            return
        except sa.exc.OperationalError as error:
            if not _is_lock_timeout(error) or attempt == len(CREATE_RETRY_DELAYS):
                raise
            time.sleep(CREATE_RETRY_DELAYS[attempt])


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            _preflight_offline_invalid_index()
            _create_index_with_bounded_retry()
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        finally:
            op.execute("RESET lock_timeout")
