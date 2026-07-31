"""Project Timeline coverage proof outside the inventory summary TOAST value.

Revision ID: 20260719_0500
Revises: 20260718_0400
Create Date: 2026-07-19 10:00:00
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260719_0500"
down_revision: str | None = "20260718_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_inventory_snapshots_timeline_capture_projection"
OLD_INDEX_NAME = "ix_inventory_snapshots_timeline_capture_observed"
COLUMN_NAME = "event_capture"
LOCK_TIMEOUT = "5s"
CREATE_RETRY_DELAYS = (2.0, 4.0, 8.0)
CAPTURE_PATH = "summary -> 'summary' -> 'kubernetes_event_capture'"
INDEX_PREDICATE = (
    "status != 'ignored_stale' "
    f"AND {COLUMN_NAME} IS NOT NULL "
    "AND event_capture_observed_at IS NOT NULL"
)


def _drop_invalid_index(index_name: str) -> None:
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
            {"index_name": index_name},
        )
        .scalar_one()
    )
    if invalid:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")


def _is_lock_timeout(error: sa.exc.OperationalError) -> bool:
    original = getattr(error, "orig", None)
    return getattr(original, "sqlstate", None) == "55P03" or "lock timeout" in str(error).casefold()


def _create_index_with_bounded_retry(index_name: str, definition: str) -> None:
    statement = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} {definition}"
    if op.get_context().as_sql:
        op.execute(statement)
        return
    for attempt in range(len(CREATE_RETRY_DELAYS) + 1):
        _drop_invalid_index(index_name)
        try:
            op.execute(statement)
            return
        except sa.exc.OperationalError as error:
            if not _is_lock_timeout(error) or attempt == len(CREATE_RETRY_DELAYS):
                raise
            time.sleep(CREATE_RETRY_DELAYS[attempt])


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE cluster_inventory_snapshots ADD COLUMN IF NOT EXISTS {COLUMN_NAME} JSONB"
    )
    op.execute(
        "UPDATE cluster_inventory_snapshots "
        f"SET {COLUMN_NAME} = {CAPTURE_PATH} "
        f"WHERE {COLUMN_NAME} IS NULL AND {CAPTURE_PATH} IS NOT NULL"
    )
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            _create_index_with_bounded_retry(
                INDEX_NAME,
                "ON cluster_inventory_snapshots "
                "(workspace_id, cluster_id, event_capture_observed_at, "
                "collected_at, created_at, snapshot_id) "
                "INCLUDE (status, event_capture) "
                f"WHERE {INDEX_PREDICATE}",
            )
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {OLD_INDEX_NAME}")
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            _create_index_with_bounded_retry(
                OLD_INDEX_NAME,
                "ON cluster_inventory_snapshots "
                "(workspace_id, cluster_id, event_capture_observed_at, "
                "collected_at, created_at, snapshot_id) "
                "WHERE status != 'ignored_stale' "
                "AND summary['summary']['kubernetes_event_capture'] IS NOT NULL "
                "AND event_capture_observed_at IS NOT NULL",
            )
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        finally:
            op.execute("RESET lock_timeout")
    op.execute(f"ALTER TABLE cluster_inventory_snapshots DROP COLUMN IF EXISTS {COLUMN_NAME}")
