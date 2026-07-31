"""Add online indexes for bounded inventory retention.

Revision ID: 20260722_0610
Revises: 20260722_0600
Create Date: 2026-07-22 06:10:00

The revision janitor used one correlated ``NOT EXISTS`` containing an ``OR``
over both lifecycle edges.  PostgreSQL could not probe either edge efficiently
and planned a scan of the complete version history for every candidate
revision.  The application query now uses two equivalent anti-joins, each
backed by one lifecycle index.  The revision index bounds the oldest-first
candidate lookup before either anti-join runs.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260722_0610"
down_revision: str | None = "20260722_0600"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "30s"
CREATE_RETRY_DELAYS = (2.0, 4.0, 8.0)
INDEX_STATEMENTS = (
    (
        "ix_inventory_versions_retention_valid_from",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_inventory_versions_retention_valid_from "
        "ON inventory_resource_versions (valid_from_revision)",
    ),
    (
        "ix_inventory_versions_retention_valid_to",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_inventory_versions_retention_valid_to "
        "ON inventory_resource_versions (valid_to_revision) "
        "WHERE valid_to_revision IS NOT NULL",
    ),
    (
        "ix_inventory_filter_revisions_retention",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_inventory_filter_revisions_retention "
        "ON inventory_filter_revisions (observed_at, revision_id)",
    ),
)


def _drop_invalid_index(index_name: str) -> None:
    """Remove only a failed concurrent-build remnant, never a valid index."""
    if op.get_context().as_sql:
        return
    invalid = bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS("
                "SELECT 1 FROM pg_catalog.pg_class AS relation "
                "JOIN pg_catalog.pg_namespace AS namespace "
                "ON namespace.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_index AS index_state "
                "ON index_state.indexrelid = relation.oid "
                "WHERE namespace.nspname = current_schema() "
                "AND relation.relname = :index_name "
                "AND index_state.indisvalid IS FALSE)"
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


def _create_index_with_bounded_retry(index_name: str, statement: str) -> None:
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
    # CONCURRENTLY is illegal inside Alembic's surrounding transaction.  The
    # autocommit block preserves ingestion availability on the multi-million
    # row history table.
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            for index_name, statement in INDEX_STATEMENTS:
                _create_index_with_bounded_retry(index_name, statement)
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            for index_name, _statement in reversed(INDEX_STATEMENTS):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
        finally:
            op.execute("RESET lock_timeout")
