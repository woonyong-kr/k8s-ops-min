"""Transactional, bounded deletion of demo-only transient data."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, exists, func, or_, select, tuple_, update
from sqlalchemy.engine import Connection
from sqlalchemy.sql.schema import Table

from domains.ai.models import AiLlmInvocationMetric
from domains.command.models import (
    AgentCommand,
    AgentCommandAttempt,
    CommandControlAction,
    CommandOperationEvent,
    CommandOperationEventCursor,
)
from domains.dashboard.models import RcaTimeline
from domains.inventory.models import (
    ClusterInventoryResourceRecord,
    ClusterInventorySnapshotRecord,
    ClusterUsageSampleRecord,
)
from domains.inventory_filter.models import (
    InventoryFilterRevision,
    InventoryResourceApplicationVersion,
    InventoryResourceLabelVersion,
    InventoryResourceVersion,
)
from domains.rca.models import (
    Evidence,
    IncidentSignalClaim,
    RcaBacklogItem,
    RcaReport,
    RecoveryPlanRecord,
)
from domains.rca_changes.models import WorkloadChange
from domains.target.evidence_jobs import (
    EVIDENCE_JOB_STATUS_COMPLETED,
    EVIDENCE_JOB_STATUS_FAILED,
)
from domains.target.models import (
    AgentPolicyStatusRecord,
    AgentReconcileStatusRecord,
    ClusterAgentStatusRecord,
    EvidenceJob,
    EvidenceWindow,
    TargetReconcileRecord,
)
from domains.timeline.models import TimelineLedgerCursor, TimelineLedgerEvent
from packages.config.constants import CommandStatus
from packages.storage.engine import DatabaseConnection
from packages.storage.schema import EventDeadLetter, EventModel, EventProcessing

TERMINAL_COMMAND_STATUSES = (
    CommandStatus.COMPLETED,
    CommandStatus.FAILED,
    CommandStatus.CANCELLED,
)
TERMINAL_EVIDENCE_JOB_STATUSES = (
    EVIDENCE_JOB_STATUS_COMPLETED,
    EVIDENCE_JOB_STATUS_FAILED,
)
TERMINAL_DEAD_LETTER_STATUSES = ("replayed", "archived")


class DemoRetentionRepository(DatabaseConnection):
    """Delete one bounded batch per explicitly selected transient scope."""

    def delete_demo_data_older_than(
        self,
        cutoff: datetime,
        *,
        scopes: tuple[str, ...],
        limit: int,
    ) -> dict[str, int]:
        bounded_limit = max(1, min(int(limit), 10_000))
        requested = frozenset(scopes)
        deleted: dict[str, int] = {}
        # One connection context is one transaction. Any failed statement rolls
        # back every table in this sweep and the next interval retries safely.
        with self.connection() as conn:
            if "commands" in requested:
                self._delete_commands(conn, cutoff, bounded_limit, deleted)
            if "incidents" in requested:
                self._delete_incidents(conn, cutoff, bounded_limit, deleted)
            if "rca" in requested:
                self._delete_rca(conn, cutoff, bounded_limit, deleted)
            if "evidence" in requested:
                self._delete_evidence(conn, cutoff, bounded_limit, deleted)
            if "timeline" in requested:
                self._delete_timeline(conn, cutoff, bounded_limit, deleted)
            if "events" in requested:
                self._delete_events(conn, cutoff, bounded_limit, deleted)
            if "projections" in requested:
                self._delete_projections(conn, cutoff, bounded_limit, deleted)
            if "observations" in requested:
                self._delete_observations(conn, cutoff, bounded_limit, deleted)
        return deleted

    def _delete_commands(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        command = AgentCommand.__table__
        old_terminal = and_(
            command.c.status.in_(TERMINAL_COMMAND_STATUSES),
            command.c.updated_at < cutoff,
        )
        children = (
            CommandOperationEvent.__table__,
            CommandOperationEventCursor.__table__,
            CommandControlAction.__table__,
            AgentCommandAttempt.__table__,
        )
        for child in children:
            _record(
                deleted,
                child.name,
                _delete_joined_batch(
                    conn,
                    child,
                    child.c.command_id,
                    command,
                    command.c.command_id,
                    parent_predicate=old_terminal,
                    limit=limit,
                ),
            )
        no_children = [
            ~exists(select(1).where(child.c.command_id == command.c.command_id))
            for child in children
        ]
        _record(
            deleted,
            command.name,
            _delete_batch(
                conn,
                command,
                command.c.updated_at,
                cutoff,
                limit=limit,
                predicates=(command.c.status.in_(TERMINAL_COMMAND_STATUSES), *no_children),
            ),
        )

    def _delete_incidents(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        _delete_table_batch(
            conn, RcaTimeline.__table__, RcaTimeline.__table__.c.updated_at, cutoff, limit, deleted
        )
        _delete_table_batch(
            conn,
            IncidentSignalClaim.__table__,
            IncidentSignalClaim.__table__.c.created_at,
            cutoff,
            limit,
            deleted,
        )

    def _delete_rca(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        for table, column in (
            (RecoveryPlanRecord.__table__, RecoveryPlanRecord.__table__.c.updated_at),
            (RcaBacklogItem.__table__, RcaBacklogItem.__table__.c.updated_at),
            (RcaReport.__table__, RcaReport.__table__.c.created_at),
        ):
            _delete_table_batch(conn, table, column, cutoff, limit, deleted)

    def _delete_evidence(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        job = EvidenceJob.__table__
        _record(
            deleted,
            job.name,
            _delete_batch(
                conn,
                job,
                job.c.updated_at,
                cutoff,
                limit=limit,
                predicates=(job.c.status.in_(TERMINAL_EVIDENCE_JOB_STATUSES),),
            ),
        )
        window = EvidenceWindow.__table__
        _record(
            deleted,
            window.name,
            _delete_batch(
                conn,
                window,
                window.c.updated_at,
                cutoff,
                limit=limit,
                predicates=(~exists(select(1).where(job.c.evidence_key == window.c.evidence_key)),),
            ),
        )
        _delete_table_batch(
            conn, Evidence.__table__, Evidence.__table__.c.created_at, cutoff, limit, deleted
        )

    def _delete_timeline(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        event = TimelineLedgerEvent.__table__
        rows = _delete_batch_rows(conn, event, event.c.occurred_at, cutoff, limit=limit)
        _record(deleted, event.name, len(rows))
        workspaces = sorted({str(row[0]) for row in rows})
        if not workspaces:
            return
        cursor = TimelineLedgerCursor.__table__
        earliest_remaining = (
            select(func.min(event.c.sequence))
            .where(event.c.workspace_id == cursor.c.workspace_id)
            .scalar_subquery()
        )
        next_retained = func.coalesce(earliest_remaining, cursor.c.last_sequence + 1)
        conn.execute(
            update(cursor)
            .where(cursor.c.workspace_id.in_(workspaces))
            .values(
                retained_from_sequence=func.greatest(
                    cursor.c.retained_from_sequence,
                    next_retained,
                )
            )
        )

    def _delete_events(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        for table, column, predicates in (
            (EventProcessing.__table__, EventProcessing.__table__.c.created_at, ()),
            (
                EventDeadLetter.__table__,
                EventDeadLetter.__table__.c.created_at,
                (EventDeadLetter.__table__.c.status.in_(TERMINAL_DEAD_LETTER_STATUSES),),
            ),
            (EventModel.__table__, EventModel.__table__.c.created_at, ()),
        ):
            _record(
                deleted,
                table.name,
                _delete_batch(
                    conn,
                    table,
                    column,
                    cutoff,
                    limit=limit,
                    predicates=predicates,
                ),
            )

    def _delete_projections(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        version = InventoryResourceVersion.__table__
        live_resource = ClusterInventoryResourceRecord.__table__
        expired_version = and_(
            version.c.observed_at < cutoff,
            or_(
                version.c.valid_to_revision.is_not(None),
                ~exists(select(1).where(live_resource.c.inventory_key == version.c.inventory_key)),
            ),
        )
        children = (
            InventoryResourceLabelVersion.__table__,
            InventoryResourceApplicationVersion.__table__,
        )
        for child in children:
            _record(
                deleted,
                child.name,
                _delete_joined_batch(
                    conn,
                    child,
                    child.c.version_id,
                    version,
                    version.c.version_id,
                    parent_predicate=expired_version,
                    limit=limit,
                ),
            )
        _record(
            deleted,
            version.name,
            _delete_batch(
                conn,
                version,
                version.c.observed_at,
                cutoff,
                limit=limit,
                predicates=(
                    or_(
                        version.c.valid_to_revision.is_not(None),
                        ~exists(
                            select(1).where(
                                live_resource.c.inventory_key == version.c.inventory_key
                            )
                        ),
                    ),
                    *(
                        ~exists(select(1).where(child.c.version_id == version.c.version_id))
                        for child in children
                    ),
                ),
            ),
        )
        revision = InventoryFilterRevision.__table__
        _record(
            deleted,
            revision.name,
            _delete_batch(
                conn,
                revision,
                revision.c.observed_at,
                cutoff,
                limit=limit,
                predicates=(
                    ~exists(
                        select(1).where(version.c.valid_from_revision == revision.c.revision_id)
                    ),
                    ~exists(select(1).where(version.c.valid_to_revision == revision.c.revision_id)),
                ),
            ),
        )
        for table, column in (
            (AiLlmInvocationMetric.__table__, AiLlmInvocationMetric.__table__.c.created_at),
            (WorkloadChange.__table__, WorkloadChange.__table__.c.changed_at),
            (TargetReconcileRecord.__table__, TargetReconcileRecord.__table__.c.updated_at),
        ):
            _delete_table_batch(conn, table, column, cutoff, limit, deleted)

    def _delete_observations(
        self,
        conn: Connection,
        cutoff: datetime,
        limit: int,
        deleted: dict[str, int],
    ) -> None:
        for table, column in (
            (
                ClusterInventoryResourceRecord.__table__,
                ClusterInventoryResourceRecord.__table__.c.last_seen_at,
            ),
            (ClusterUsageSampleRecord.__table__, ClusterUsageSampleRecord.__table__.c.sampled_at),
            (ClusterAgentStatusRecord.__table__, ClusterAgentStatusRecord.__table__.c.last_seen_at),
            (AgentPolicyStatusRecord.__table__, AgentPolicyStatusRecord.__table__.c.created_at),
            (
                AgentReconcileStatusRecord.__table__,
                AgentReconcileStatusRecord.__table__.c.created_at,
            ),
        ):
            _delete_table_batch(conn, table, column, cutoff, limit, deleted)

        snapshot = ClusterInventorySnapshotRecord.__table__
        resource = ClusterInventoryResourceRecord.__table__
        usage = ClusterUsageSampleRecord.__table__
        revision = InventoryFilterRevision.__table__
        _record(
            deleted,
            snapshot.name,
            _delete_batch(
                conn,
                snapshot,
                snapshot.c.collected_at,
                cutoff,
                limit=limit,
                predicates=(
                    ~exists(select(1).where(resource.c.snapshot_id == snapshot.c.snapshot_id)),
                    ~exists(select(1).where(usage.c.snapshot_id == snapshot.c.snapshot_id)),
                    ~exists(select(1).where(revision.c.snapshot_id == snapshot.c.snapshot_id)),
                ),
            ),
        )


def _delete_table_batch(
    conn: Connection,
    table: Table,
    cutoff_column: Any,
    cutoff: datetime,
    limit: int,
    deleted: dict[str, int],
) -> None:
    _record(
        deleted,
        table.name,
        _delete_batch(conn, table, cutoff_column, cutoff, limit=limit),
    )


def _delete_batch(
    conn: Connection,
    table: Table,
    cutoff_column: Any,
    cutoff: datetime,
    *,
    limit: int,
    predicates: Sequence[Any] = (),
) -> int:
    return len(
        _delete_batch_rows(
            conn,
            table,
            cutoff_column,
            cutoff,
            limit=limit,
            predicates=predicates,
        )
    )


def _delete_batch_rows(
    conn: Connection,
    table: Table,
    cutoff_column: Any,
    cutoff: datetime,
    *,
    limit: int,
    predicates: Sequence[Any] = (),
) -> list[Any]:
    primary_keys = tuple(table.primary_key.columns)
    expired = (
        select(*primary_keys)
        .where(cutoff_column < cutoff, *predicates)
        .order_by(cutoff_column, *primary_keys)
        .limit(limit)
        .cte(f"expired_demo_{table.name}")
    )
    statement = (
        delete(table).where(_primary_key_membership(primary_keys, expired)).returning(*primary_keys)
    )
    return list(conn.execute(statement).all())


def _delete_joined_batch(
    conn: Connection,
    child: Table,
    child_parent_key: Any,
    parent: Table,
    parent_key: Any,
    *,
    parent_predicate: Any,
    limit: int,
) -> int:
    primary_keys = tuple(child.primary_key.columns)
    expired = (
        select(*primary_keys)
        .select_from(child.join(parent, child_parent_key == parent_key))
        .where(parent_predicate)
        .order_by(*primary_keys)
        .limit(limit)
        .cte(f"expired_demo_{child.name}")
    )
    statement = (
        delete(child).where(_primary_key_membership(primary_keys, expired)).returning(*primary_keys)
    )
    return len(conn.execute(statement).all())


def _primary_key_membership(primary_keys: tuple[Any, ...], expired: Any) -> Any:
    expired_columns = tuple(expired.c[column.name] for column in primary_keys)
    if len(primary_keys) == 1:
        return primary_keys[0].in_(select(expired_columns[0]))
    return tuple_(*primary_keys).in_(select(*expired_columns))


def _record(deleted: dict[str, int], table_name: str, count: int) -> None:
    if count:
        deleted[table_name] = deleted.get(table_name, 0) + count
