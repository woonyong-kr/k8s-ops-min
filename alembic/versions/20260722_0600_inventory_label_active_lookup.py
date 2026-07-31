"""Add the covering lookup used by active inventory label search.

Revision ID: 20260722_0600
Revises: 20260719_0500
Create Date: 2026-07-22 06:00:00

The label-version table is immutable and does not carry the parent version's
``valid_to_revision`` value, so an honest active-only partial label index cannot
be expressed without denormalizing lifecycle state.  Instead, the query starts
from the small active resource set and probes this covering index by version id.
The companion partial covering index keeps that active set heap-free. Concurrent
builds avoid blocking projection ingestion on the historical tables.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260722_0600"
down_revision: str | None = "20260719_0500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LABEL_INDEX_NAME = "ix_inventory_label_versions_active_lookup"
VERSION_INDEX_NAME = "ix_inventory_versions_active_facets"
LOCK_TIMEOUT = "30s"
CREATE_RETRY_DELAYS = (2.0, 4.0, 8.0)


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
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            _create_index_with_bounded_retry(
                VERSION_INDEX_NAME,
                "ON inventory_resource_versions (workspace_id, cluster_id) "
                "INCLUDE (version_id, inventory_key, resource_type, kind, namespace, "
                "name, health, search_text) WHERE valid_to_revision IS NULL",
            )
            _create_index_with_bounded_retry(
                LABEL_INDEX_NAME,
                "ON inventory_resource_label_versions (version_id, workspace_id) "
                "INCLUDE (selector, key, value)",
            )
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {LABEL_INDEX_NAME}")
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {VERSION_INDEX_NAME}")
        finally:
            op.execute("RESET lock_timeout")
