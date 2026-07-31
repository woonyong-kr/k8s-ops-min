"""Add bounded lookup indexes for the Resources change timeline.

Revision ID: 20260716_1100
Revises: 20260716_1000
Create Date: 2026-07-16 22:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260716_1100"
down_revision: str | None = "20260716_1000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONCURRENT_INDEXES = (
    "ix_timeline_events_inventory_changes",
    "ix_inventory_filter_revisions_change_coverage",
)


def _preflight_offline_invalid_indexes() -> None:
    """Make an offline script fail explicitly instead of preserving an invalid remnant."""
    if not op.get_context().as_sql:
        return
    names = ", ".join(f"'{name}'" for name in CONCURRENT_INDEXES)
    op.execute(
        "DO $opsia$ "
        "DECLARE invalid_indexes TEXT; "
        "BEGIN "
        "SELECT string_agg(relation.relname, ', ' ORDER BY relation.relname) "
        "INTO invalid_indexes "
        "FROM pg_catalog.pg_class AS relation "
        "JOIN pg_catalog.pg_namespace AS namespace "
        "ON namespace.oid = relation.relnamespace "
        "JOIN pg_catalog.pg_index AS index_state "
        "ON index_state.indexrelid = relation.oid "
        "WHERE namespace.nspname = current_schema() "
        f"AND relation.relname IN ({names}) "
        "AND index_state.indisvalid IS FALSE; "
        "IF invalid_indexes IS NOT NULL THEN "
        "RAISE EXCEPTION 'invalid concurrent index remnant: %; "
        "run the migration online to clean it safely', invalid_indexes; "
        "END IF; "
        "END $opsia$"
    )


def _drop_invalid_index(index_name: str) -> None:
    """Remove a failed concurrent-build remnant without replacing a valid index."""
    context = op.get_context()
    if context.as_sql:
        # Offline SQL cannot distinguish a valid index from a failed concurrent build.
        # Never destroy a valid production index merely to render an offline script.
        return
    invalid = bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_catalog.pg_class AS relation "
                "JOIN pg_catalog.pg_namespace AS namespace "
                "ON namespace.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_index AS index_state "
                "ON index_state.indexrelid = relation.oid "
                "WHERE namespace.nspname = current_schema() "
                "AND relation.relname = :index_name "
                "AND index_state.indisvalid IS FALSE"
                ")"
            ),
            {"index_name": index_name},
        )
        .scalar_one()
    )
    if invalid:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _preflight_offline_invalid_indexes()
            op.execute(
                sa.text(
                    "ALTER TABLE inventory_filter_revisions "
                    "ADD COLUMN IF NOT EXISTS change_ledger_epoch TEXT"
                )
            )
            _drop_invalid_index("ix_timeline_events_inventory_changes")
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_timeline_events_inventory_changes "
                "ON timeline_events (workspace_id, cluster_id, occurred_at, event_id) "
                "WHERE source = 'inventory' AND activity = 'change' "
                "AND event_type IN ('add', 'update', 'delete')"
            )
            _drop_invalid_index("ix_inventory_filter_revisions_change_coverage")
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_inventory_filter_revisions_change_coverage "
                "ON inventory_filter_revisions "
                "(workspace_id, cluster_id, change_ledger_epoch, resources_complete, "
                "observed_at, revision_id)"
            )
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute(
                "DROP INDEX CONCURRENTLY IF EXISTS ix_inventory_filter_revisions_change_coverage"
            )
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_timeline_events_inventory_changes")
            op.execute(
                sa.text(
                    "ALTER TABLE inventory_filter_revisions "
                    "DROP COLUMN IF EXISTS change_ledger_epoch"
                )
            )
        finally:
            op.execute("RESET lock_timeout")
