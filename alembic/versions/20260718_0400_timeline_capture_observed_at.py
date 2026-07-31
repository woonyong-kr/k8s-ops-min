"""Index Timeline coverage by the capture's semantic observation time.

Revision ID: 20260718_0400
Revises: 20260718_0300
Create Date: 2026-07-18 15:30:00
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260718_0400"
down_revision: str | None = "20260718_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_INDEX_NAME = "ix_inventory_snapshots_timeline_coverage"
INDEX_NAME = "ix_inventory_snapshots_timeline_capture_observed"
COLUMN_NAME = "event_capture_observed_at"
LOCK_TIMEOUT = "5s"
CREATE_RETRY_DELAYS = (2.0, 4.0, 8.0)
CAPTURE_PATH = "summary -> 'summary' -> 'kubernetes_event_capture'"
OBSERVED_AT_TEXT = f"{CAPTURE_PATH} -> 'freshness' ->> 'observed_at'"
CANONICAL_TIMESTAMP_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})?$"
)
INDEX_PREDICATE = (
    "status != 'ignored_stale' "
    "AND summary['summary']['kubernetes_event_capture'] IS NOT NULL "
    f"AND {COLUMN_NAME} IS NOT NULL"
)


def _valid_observed_at_clause() -> str:
    normalized = f"NULLIF(BTRIM({OBSERVED_AT_TEXT}), '')"
    return (
        f"{normalized} ~ '{CANONICAL_TIMESTAMP_PATTERN}' "
        f"AND pg_input_is_valid({normalized}, 'timestamp with time zone')"
    )


def _backfill_observed_at() -> None:
    normalized = f"NULLIF(BTRIM({OBSERVED_AT_TEXT}), '')"
    op.execute(
        "UPDATE cluster_inventory_snapshots "
        f"SET {COLUMN_NAME} = {normalized}::timestamptz "
        f"WHERE {COLUMN_NAME} IS NULL "
        f"AND {CAPTURE_PATH} IS NOT NULL "
        f"AND {_valid_observed_at_clause()}"
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
        "ALTER TABLE cluster_inventory_snapshots "
        f"ADD COLUMN IF NOT EXISTS {COLUMN_NAME} TIMESTAMPTZ"
    )
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    _backfill_observed_at()
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            _create_index_with_bounded_retry(
                INDEX_NAME,
                "ON cluster_inventory_snapshots "
                "(workspace_id, cluster_id, event_capture_observed_at, "
                "collected_at, created_at, snapshot_id) "
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
                "(workspace_id, cluster_id, collected_at, created_at, snapshot_id) "
                "WHERE status != 'ignored_stale' "
                "AND summary['summary']['kubernetes_event_capture'] IS NOT NULL",
            )
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        finally:
            op.execute("RESET lock_timeout")
    op.execute(f"ALTER TABLE cluster_inventory_snapshots DROP COLUMN IF EXISTS {COLUMN_NAME}")
