"""Append-only timeline ledger repository.

PostgreSQL is the replay authority.  A later broker integration receives only
facts returned after this repository's transaction has committed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy import BigInteger, Integer, and_, cast, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.gitops.models import Application
from domains.inventory.models import ClusterInventoryResourceRecord
from domains.timeline.models import (
    TimelineLedgerCursor,
    TimelineLedgerEvent,
    TimelinePinRecord,
    TimelinePinSetRecord,
)
from domains.timeline.predicate import (
    TimelineEvidencePredicate,
    timeline_evidence_sql_predicate,
    timeline_resource_kind_sql,
)
from packages.contracts.parity import ClusterScope, ResourceRef
from packages.contracts.timeline import (
    RealtimePolicy,
    TimelineApplicationPinSnapshot,
    TimelineEvent,
    TimelineFilters,
    TimelinePin,
    TimelinePinMutation,
    TimelinePinnedApplicationSubject,
    TimelinePinnedResourceSubject,
    TimelinePinSet,
    TimelinePinSubject,
)
from packages.storage.engine import DatabaseConnection

TimelineReplayStatus = Literal["available", "resync_required"]
TimelineResyncReason = Literal["retention_boundary"]
MAX_TIMELINE_EVENTS = 10_000
TIMELINE_APPEND_CHUNK = 1_000


def _timeline_diagnostics_statement(workspace_id: str) -> Any:
    """Build one cursor lookup and two single-row time-bound probes."""
    cursor = TimelineLedgerCursor.__table__
    ledger = TimelineLedgerEvent.__table__
    anchor = select(literal(1).label("present")).subquery("diagnostics_anchor")
    oldest_occurred_at = (
        select(ledger.c.occurred_at)
        .where(ledger.c.workspace_id == workspace_id)
        .order_by(ledger.c.occurred_at, ledger.c.sequence)
        .limit(1)
        .scalar_subquery()
    )
    newest_occurred_at = (
        select(ledger.c.occurred_at)
        .where(ledger.c.workspace_id == workspace_id)
        .order_by(ledger.c.occurred_at.desc(), ledger.c.sequence.desc())
        .limit(1)
        .scalar_subquery()
    )
    high_water_sequence = func.coalesce(cursor.c.last_sequence, 0)
    return select(
        high_water_sequence.label("event_count"),
        oldest_occurred_at.label("oldest_occurred_at"),
        newest_occurred_at.label("newest_occurred_at"),
        high_water_sequence.label("high_water_sequence"),
        func.coalesce(cursor.c.retained_from_sequence, 1).label("retained_from_sequence"),
    ).select_from(
        anchor.outerjoin(
            cursor,
            cursor.c.workspace_id == workspace_id,
        )
    )


class TimelinePinRevisionConflict(ValueError):
    """The browser attempted a pin mutation from an obsolete pin-set revision."""

    def __init__(self, revision: int) -> None:
        self.revision = revision
        super().__init__(f"timeline pins revision conflict (current={revision})")


@dataclass(frozen=True)
class TimelineLedgerReadScope:
    """An already-authorized read boundary; HTTP authorization is intentionally external.

    Every source has an independent grant set.  Empty sets are deny-by-default
    rather than a fallback to the selected cluster scope.
    """

    workspace_id: str
    scopes: tuple[ClusterScope, ...]
    inventory_cluster_ids: frozenset[str] = frozenset()
    kubernetes_event_cluster_ids: frozenset[str] = frozenset()
    incident_cluster_ids: frozenset[str] = frozenset()
    application_workflow_ids: frozenset[str] = frozenset()
    gitops_application_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.workspace_id:
            raise ValueError("timeline ledger scope requires a workspace")
        if not self.scopes:
            raise ValueError("timeline ledger scope requires at least one cluster scope")
        if any(scope.workspace_id != self.workspace_id for scope in self.scopes):
            raise ValueError("timeline ledger scopes must use the authorized workspace")
        for attribute in (
            "inventory_cluster_ids",
            "kubernetes_event_cluster_ids",
            "incident_cluster_ids",
            "application_workflow_ids",
            "gitops_application_ids",
        ):
            values = getattr(self, attribute)
            if any(not isinstance(value, str) for value in values):
                raise ValueError(f"timeline {attribute} must contain text identities")
            normalized = frozenset(value.strip() for value in values if value.strip())
            if len(normalized) != len(values):
                raise ValueError(f"timeline {attribute} must contain non-empty identities")
            object.__setattr__(self, attribute, normalized)


@dataclass(frozen=True)
class TimelineLedgerAppend:
    event: TimelineEvent
    sequence: int
    inserted: bool

    @property
    def record(self) -> TimelineLedgerRecord:
        return TimelineLedgerRecord(sequence=self.sequence, event=self.event)


@dataclass(frozen=True)
class TimelineLedgerRecord:
    """One internal replay position paired with its immutable timeline event.

    The sequence is deliberately a domain-storage value, never a transport
    field.  A gateway converts it to a user/scope-bound opaque cursor.
    """

    sequence: int
    event: TimelineEvent

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("timeline ledger record sequence must be positive")


@dataclass(frozen=True)
class TimelineLedgerSnapshot:
    records: tuple[TimelineLedgerRecord, ...]
    high_water_sequence: int
    retained_from_sequence: int
    truncated: bool = False

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        """Compatibility convenience; cursor issuance must use ``records``."""
        return tuple(record.event for record in self.records)


@dataclass(frozen=True)
class TimelineReplayResult:
    status: TimelineReplayStatus
    records: tuple[TimelineLedgerRecord, ...]
    high_water_sequence: int
    retained_from_sequence: int
    reason: TimelineResyncReason | None = None

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        """Compatibility convenience; cursor issuance must use ``records``."""
        return tuple(record.event for record in self.records)


@dataclass(frozen=True)
class TimelineOverviewBucketAggregate:
    """One internal aggregate; the HTTP adapter turns indexes into safe ranges."""

    bucket_index: int
    event_count: int
    problem_count: int


@dataclass(frozen=True)
class TimelineOverviewAggregate:
    """No raw evidence crosses this repository aggregate boundary."""

    buckets: tuple[TimelineOverviewBucketAggregate, ...]
    activity_counts: dict[str, int]
    kind_counts: dict[str, int]
    new_evidence_count: int | None


class TimelinePolicyProvider(Protocol):
    """Supplies server policy; clients never choose their own stream budgets."""

    def policy_for(self, scope: TimelineLedgerReadScope) -> RealtimePolicy: ...


class TimelineEventFanout(Protocol):
    """Optional post-commit announcement boundary; no broker is implemented here."""

    async def publish_committed(self, append: TimelineLedgerAppend) -> None: ...


class TimelineLedgerRepository(DatabaseConnection):
    """Persist and replay immutable source evidence in workspace-local sequence order."""

    def read_timeline_pin_set(self, workspace_id: str, user_id: str) -> TimelinePinSet:
        """Read the owner's complete stored set; callers remove currently unreadable rows."""
        with self.connection() as conn:
            return _read_timeline_pin_set(conn, workspace_id=workspace_id, user_id=user_id)

    def resolve_timeline_pin_resource(
        self,
        *,
        workspace_id: str,
        cluster_id: str,
        uid: str,
    ) -> TimelinePinnedResourceSubject | None:
        """Materialize one exact inventory UID without trusting browser display fields."""
        resource = ClusterInventoryResourceRecord.__table__
        statement = (
            select(
                resource.c.api_version,
                resource.c.kind,
                resource.c.namespace,
                resource.c.name,
                resource.c.uid,
            )
            .where(
                resource.c.workspace_id == workspace_id,
                resource.c.cluster_id == cluster_id,
                resource.c.uid == uid,
            )
            .order_by(resource.c.last_seen_at.desc(), resource.c.inventory_key.desc())
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().one_or_none()
        if row is None or not isinstance(row["uid"], str) or not row["uid"].strip():
            return None
        api_group, version = _split_api_version(str(row["api_version"] or ""))
        return TimelinePinnedResourceSubject(
            scope=ClusterScope(workspace_id=workspace_id, cluster_id=cluster_id),
            resource=ResourceRef(
                api_group=api_group,
                version=version,
                kind=str(row["kind"]),
                namespace=str(row["namespace"]) if row["namespace"] is not None else None,
                name=str(row["name"]),
                uid=str(row["uid"]),
            ),
        )

    def resolve_timeline_pin_application(
        self,
        *,
        workspace_id: str,
        application_id: str,
    ) -> TimelinePinnedApplicationSubject | None:
        """Materialize immutable application display facts from the authorized workspace row."""
        application = Application.__table__
        statement = (
            select(
                application.c.application_id,
                application.c.name,
                application.c.repository_id,
                application.c.manifest_path,
            )
            .where(
                application.c.workspace_id == workspace_id,
                application.c.application_id == application_id,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        return TimelinePinnedApplicationSubject(
            application_id=str(row["application_id"]),
            snapshot=TimelineApplicationPinSnapshot(
                name=str(row["name"]),
                repository_id=str(row["repository_id"]),
                manifest_path=str(row["manifest_path"]),
            ),
        )

    def put_timeline_pin(
        self,
        *,
        workspace_id: str,
        user_id: str,
        expected_revision: int,
        subject: TimelinePinSubject,
    ) -> TimelinePinMutation:
        """Add one materialized subject once, guarded by a row lock and optimistic revision."""
        if expected_revision < 0:
            raise ValueError("timeline pin revision must be non-negative")
        pins = TimelinePinRecord.__table__
        subject_key = _timeline_pin_subject_key(subject)
        with self.unit_of_work():
            with self.connection() as conn:
                revision = _lock_timeline_pin_set(conn, workspace_id=workspace_id, user_id=user_id)
                if revision != expected_revision:
                    raise TimelinePinRevisionConflict(revision)
                existing = conn.execute(
                    select(pins.c.pin_id).where(
                        pins.c.workspace_id == workspace_id,
                        pins.c.user_id == user_id,
                        pins.c.subject_key == subject_key,
                    )
                ).scalar_one_or_none()
                action: Literal["added", "unchanged"] = "unchanged"
                if existing is None:
                    conn.execute(
                        pg_insert(pins).values(
                            workspace_id=workspace_id,
                            user_id=user_id,
                            pin_id=uuid.uuid4().hex,
                            subject_key=subject_key,
                            subject=subject.model_dump(mode="json"),
                        )
                    )
                    revision = _increment_timeline_pin_revision(
                        conn, workspace_id=workspace_id, user_id=user_id
                    )
                    action = "added"
                pin_set = _read_timeline_pin_set(conn, workspace_id=workspace_id, user_id=user_id)
        return TimelinePinMutation(action=action, pin_set=pin_set)

    def delete_timeline_pin(
        self,
        *,
        workspace_id: str,
        user_id: str,
        pin_id: str,
        expected_revision: int,
    ) -> TimelinePinMutation:
        """Idempotently delete only the caller's row; an absent ID intentionally keeps revision."""
        if expected_revision < 0:
            raise ValueError("timeline pin revision must be non-negative")
        pins = TimelinePinRecord.__table__
        with self.unit_of_work():
            with self.connection() as conn:
                revision = _lock_timeline_pin_set(conn, workspace_id=workspace_id, user_id=user_id)
                if revision != expected_revision:
                    raise TimelinePinRevisionConflict(revision)
                deleted = conn.execute(
                    delete(pins)
                    .where(
                        pins.c.workspace_id == workspace_id,
                        pins.c.user_id == user_id,
                        pins.c.pin_id == pin_id,
                    )
                    .returning(pins.c.pin_id)
                ).scalar_one_or_none()
                action: Literal["deleted", "absent"] = "absent"
                if deleted is not None:
                    _increment_timeline_pin_revision(
                        conn, workspace_id=workspace_id, user_id=user_id
                    )
                    action = "deleted"
                pin_set = _read_timeline_pin_set(conn, workspace_id=workspace_id, user_id=user_id)
        return TimelinePinMutation(action=action, pin_set=pin_set)

    def append_timeline_event(self, event: TimelineEvent) -> TimelineLedgerAppend:
        """Append one event through the same atomic bulk allocation path."""
        return self.append_timeline_events((event,))[0]

    def append_timeline_events(
        self, events: Iterable[TimelineEvent]
    ) -> tuple[TimelineLedgerAppend, ...]:
        """Allocate one contiguous sequence range and bulk-insert source-unique facts."""
        batch = tuple(events)
        if not batch:
            return ()
        workspace_ids = {event.scope.workspace_id for event in batch}
        if len(workspace_ids) != 1:
            raise ValueError("timeline bulk append requires one workspace")

        cursor = TimelineLedgerCursor.__table__
        ledger = TimelineLedgerEvent.__table__
        workspace_id = next(iter(workspace_ids))
        source_keys = tuple(dict.fromkeys(event.source_key for event in batch))
        first_events = {event.source_key: event for event in reversed(batch)}
        with self.connection() as conn:
            conn.execute(
                pg_insert(cursor)
                .values(workspace_id=workspace_id, last_sequence=0, retained_from_sequence=1)
                .on_conflict_do_nothing(index_elements=[cursor.c.workspace_id])
            )
            conn.execute(
                select(cursor.c.last_sequence, cursor.c.retained_from_sequence)
                .where(cursor.c.workspace_id == workspace_id)
                .with_for_update()
            ).mappings().one()
            existing_rows = {
                str(row["source_key"]): dict(row)
                for row in conn.execute(
                    select(ledger).where(
                        ledger.c.workspace_id == workspace_id,
                        ledger.c.source_key.in_(source_keys),
                    )
                )
                .mappings()
                .all()
            }
            new_events = [
                first_events[source_key]
                for source_key in source_keys
                if source_key not in existing_rows
            ]
            inserted_rows: dict[str, dict[str, Any]] = {}
            if new_events:
                high_water = int(
                    conn.execute(
                        update(cursor)
                        .where(cursor.c.workspace_id == workspace_id)
                        .values(last_sequence=cursor.c.last_sequence + len(new_events))
                        .returning(cursor.c.last_sequence)
                    ).scalar_one()
                )
                first_sequence = high_water - len(new_events) + 1
                values = [
                    _event_values(event, sequence=first_sequence + index)
                    for index, event in enumerate(new_events)
                ]
                for start in range(0, len(values), TIMELINE_APPEND_CHUNK):
                    rows = (
                        conn.execute(
                            pg_insert(ledger)
                            .values(values[start : start + TIMELINE_APPEND_CHUNK])
                            .returning(*ledger.c)
                        )
                        .mappings()
                        .all()
                    )
                    inserted_rows.update((str(row["source_key"]), dict(row)) for row in rows)

        rows_by_key = {**existing_rows, **inserted_rows}
        reported_insertions: set[str] = set()
        appends: list[TimelineLedgerAppend] = []
        for event in batch:
            row = rows_by_key[event.source_key]
            inserted = (
                event.source_key in inserted_rows and event.source_key not in reported_insertions
            )
            reported_insertions.add(event.source_key)
            appends.append(
                TimelineLedgerAppend(
                    event=_event_from_row(row),
                    sequence=int(row["sequence"]),
                    inserted=inserted,
                )
            )
        return tuple(appends)

    def snapshot_timeline_events(
        self,
        read_scope: TimelineLedgerReadScope,
        *,
        predicate: TimelineEvidencePredicate,
        limit: int = 1_000,
    ) -> TimelineLedgerSnapshot:
        """Read one scoped history snapshot from one cursor-consistent transaction."""
        _validate_requested_limit(limit)
        with self.unit_of_work():
            cursor_state = self._cursor_state(read_scope.workspace_id)
            records = self._read_records(
                read_scope,
                after_sequence=cursor_state[1] - 1,
                through_sequence=cursor_state[0],
                predicate=predicate,
                phase="snapshot",
                limit=limit + 1,
                replay_order=False,
            )
        truncated = len(records) > limit
        if truncated:
            # Snapshot reads fetch the newest ingestion positions first and
            # are restored to replay order by ``_read_records``. Drop only the
            # oldest overflow sentinel so a late-arriving historical event is
            # never hidden behind the snapshot high-water cursor.
            records = records[1:]
        return TimelineLedgerSnapshot(
            records=records,
            high_water_sequence=cursor_state[0],
            retained_from_sequence=cursor_state[1],
            truncated=truncated,
        )

    def replay_timeline_events(
        self,
        read_scope: TimelineLedgerReadScope,
        *,
        after_sequence: int,
        predicate: TimelineEvidencePredicate,
        limit: int = 1_000,
    ) -> TimelineReplayResult:
        """Return a scoped suffix, or an explicit resync result after retention expiry."""
        if isinstance(after_sequence, bool) or after_sequence < 0:
            raise ValueError("timeline replay sequence must be non-negative")
        _validate_requested_limit(limit)
        high_water, retained_from = self._cursor_state(read_scope.workspace_id)
        if after_sequence < retained_from - 1:
            return TimelineReplayResult(
                status="resync_required",
                records=(),
                high_water_sequence=high_water,
                retained_from_sequence=retained_from,
                reason="retention_boundary",
            )
        return TimelineReplayResult(
            status="available",
            records=self._read_records(
                read_scope,
                after_sequence=after_sequence,
                through_sequence=high_water,
                predicate=predicate,
                phase="stream",
                limit=limit,
                replay_order=True,
            ),
            high_water_sequence=high_water,
            retained_from_sequence=retained_from,
        )

    def timeline_overview(
        self,
        read_scope: TimelineLedgerReadScope,
        *,
        predicate: TimelineEvidencePredicate,
        bucket_width_ms: int,
    ) -> TimelineOverviewAggregate:
        """Read a bounded retained-strip aggregate under the snapshot predicate.

        Each statement applies the same scope and source grants as the event
        reader.  Facet axes remove only their own active filter, which keeps
        source authorization, namespace, search, deletion, and the opposite
        facet selection intact.
        """
        if bucket_width_ms < 1_000:
            raise ValueError("timeline overview bucket width is invalid")
        with self.unit_of_work():
            bucket_rows = self._overview_rows(
                _timeline_overview_buckets_statement(
                    read_scope,
                    predicate=predicate,
                    bucket_width_ms=bucket_width_ms,
                )
            )
            activity_predicate = predicate.with_filters(
                TimelineFilters(
                    activity=(),
                    kinds=predicate.replay_identity.filters.kinds,
                    include_deleted=predicate.replay_identity.filters.include_deleted,
                    query=predicate.replay_identity.filters.search,
                )
            )
            activity_rows = self._overview_rows(
                _timeline_overview_activity_facets_statement(
                    read_scope,
                    predicate=activity_predicate,
                )
            )
            kind_predicate = predicate.with_filters(
                TimelineFilters(
                    activity=predicate.replay_identity.filters.activity,
                    kinds=(),
                    include_deleted=predicate.replay_identity.filters.include_deleted,
                    query=predicate.replay_identity.filters.search,
                )
            )
            kind_rows = self._overview_rows(
                _timeline_overview_kind_facets_statement(
                    read_scope,
                    predicate=kind_predicate,
                )
            )
            later_predicate = predicate.after_frozen_window()
            new_evidence_count = (
                None
                if later_predicate is None
                else self._overview_count(
                    _timeline_overview_later_count_statement(
                        read_scope,
                        predicate=later_predicate,
                    )
                )
            )
        return TimelineOverviewAggregate(
            buckets=tuple(
                TimelineOverviewBucketAggregate(
                    bucket_index=int(row["bucket_index"]),
                    event_count=int(row["event_count"]),
                    problem_count=int(row["problem_count"]),
                )
                for row in bucket_rows
            ),
            activity_counts=_overview_counts(activity_rows, key="activity"),
            kind_counts=_overview_counts(kind_rows, key="kind"),
            new_evidence_count=new_evidence_count,
        )

    def advance_timeline_retention(self, workspace_id: str, *, retained_from_sequence: int) -> int:
        """Advance the replay boundary without rewriting any ledger event."""
        if not workspace_id or retained_from_sequence < 1:
            raise ValueError("timeline retention boundary is invalid")
        cursor = TimelineLedgerCursor.__table__
        with self.connection() as conn:
            conn.execute(
                pg_insert(cursor)
                .values(
                    workspace_id=workspace_id,
                    last_sequence=0,
                    retained_from_sequence=retained_from_sequence,
                )
                .on_conflict_do_nothing(index_elements=[cursor.c.workspace_id])
            )
            return int(
                conn.execute(
                    update(cursor)
                    .where(
                        cursor.c.workspace_id == workspace_id,
                        cursor.c.retained_from_sequence < retained_from_sequence,
                    )
                    .values(retained_from_sequence=retained_from_sequence)
                    .returning(cursor.c.retained_from_sequence)
                ).scalar_one_or_none()
                or conn.execute(
                    select(cursor.c.retained_from_sequence).where(
                        cursor.c.workspace_id == workspace_id
                    )
                ).scalar_one()
            )

    def timeline_diagnostics(self, workspace_id: str) -> dict[str, Any]:
        """Return exact append-only diagnostics with three bounded index probes.

        ``last_sequence`` is also the exact row count: the ledger allocates one
        contiguous sequence for every inserted row, never updates/deletes source
        facts, and advances the cursor in the same transaction as the insert.
        Reading that authority avoids aggregating every historical event.  The
        two time bounds stop at one row through the diagnostics index.
        """
        statement = _timeline_diagnostics_statement(workspace_id)
        with self.connection() as conn:
            row = conn.execute(statement).mappings().one()
        return {
            "event_count": int(row["event_count"] or 0),
            "oldest_occurred_at": row["oldest_occurred_at"],
            "newest_occurred_at": row["newest_occurred_at"],
            "high_water_sequence": int(row["high_water_sequence"] or 0),
            "retained_from_sequence": int(row["retained_from_sequence"] or 1),
        }

    def _cursor_state(self, workspace_id: str) -> tuple[int, int]:
        cursor = TimelineLedgerCursor.__table__
        with self.connection() as conn:
            row = (
                conn.execute(
                    select(cursor.c.last_sequence, cursor.c.retained_from_sequence).where(
                        cursor.c.workspace_id == workspace_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return (
            (0, 1)
            if row is None
            else (int(row["last_sequence"]), int(row["retained_from_sequence"]))
        )

    def _read_records(
        self,
        read_scope: TimelineLedgerReadScope,
        *,
        after_sequence: int,
        through_sequence: int,
        predicate: TimelineEvidencePredicate,
        phase: Literal["snapshot", "stream"],
        limit: int,
        replay_order: bool,
    ) -> tuple[TimelineLedgerRecord, ...]:
        statement = _timeline_events_statement(
            read_scope,
            after_sequence=after_sequence,
            through_sequence=through_sequence,
            predicate=predicate,
            phase=phase,
            limit=limit,
            replay_order=replay_order,
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings()
            records = tuple(_record_from_row(row) for row in rows)
            return records if replay_order else tuple(reversed(records))

    def _overview_rows(self, statement: Any) -> tuple[Any, ...]:
        with self.connection() as conn:
            return tuple(conn.execute(statement).mappings())

    def _overview_count(self, statement: Any) -> int:
        with self.connection() as conn:
            return int(conn.execute(statement).scalar_one())


def _lock_timeline_pin_set(conn: Any, *, workspace_id: str, user_id: str) -> int:
    """Create then lock an owner's revision row, so every mutation serializes exactly once."""
    pin_sets = TimelinePinSetRecord.__table__
    conn.execute(
        pg_insert(pin_sets)
        .values(workspace_id=workspace_id, user_id=user_id, revision=0)
        .on_conflict_do_nothing(index_elements=[pin_sets.c.workspace_id, pin_sets.c.user_id])
    )
    return int(
        conn.execute(
            select(pin_sets.c.revision)
            .where(
                pin_sets.c.workspace_id == workspace_id,
                pin_sets.c.user_id == user_id,
            )
            .with_for_update()
        ).scalar_one()
    )


def _increment_timeline_pin_revision(conn: Any, *, workspace_id: str, user_id: str) -> int:
    pin_sets = TimelinePinSetRecord.__table__
    return int(
        conn.execute(
            update(pin_sets)
            .where(
                pin_sets.c.workspace_id == workspace_id,
                pin_sets.c.user_id == user_id,
            )
            .values(revision=pin_sets.c.revision + 1, updated_at=func.now())
            .returning(pin_sets.c.revision)
        ).scalar_one()
    )


def _read_timeline_pin_set(conn: Any, *, workspace_id: str, user_id: str) -> TimelinePinSet:
    """Read without creating a revision row: a never-pinned user is revision zero."""
    pin_sets = TimelinePinSetRecord.__table__
    pins = TimelinePinRecord.__table__
    revision = conn.execute(
        select(pin_sets.c.revision).where(
            pin_sets.c.workspace_id == workspace_id,
            pin_sets.c.user_id == user_id,
        )
    ).scalar_one_or_none()
    rows = conn.execute(
        select(pins.c.pin_id, pins.c.subject, pins.c.created_at)
        .where(pins.c.workspace_id == workspace_id, pins.c.user_id == user_id)
        .order_by(pins.c.created_at.asc(), pins.c.pin_id.asc())
    ).mappings()
    return TimelinePinSet(
        revision=int(revision or 0),
        pins=tuple(
            TimelinePin(
                pin_id=str(row["pin_id"]),
                subject=_timeline_pin_subject_from_row(row["subject"]),
                created_at=row["created_at"],
            )
            for row in rows
        ),
    )


def _timeline_pin_subject_from_row(value: object) -> TimelinePinSubject:
    if not isinstance(value, dict):
        raise ValueError("timeline pin subject is invalid")
    kind = value.get("kind")
    if kind == "resource":
        return TimelinePinnedResourceSubject.model_validate(value)
    if kind == "application":
        return TimelinePinnedApplicationSubject.model_validate(value)
    raise ValueError("timeline pin subject kind is invalid")


def _timeline_pin_subject_key(subject: TimelinePinSubject) -> str:
    payload = json.dumps(
        subject.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _split_api_version(api_version: str) -> tuple[str, str]:
    normalized = api_version.strip()
    if "/" not in normalized:
        return "", normalized
    group, version = normalized.rsplit("/", 1)
    return group, version


def _timeline_events_statement(
    read_scope: TimelineLedgerReadScope,
    *,
    after_sequence: int,
    through_sequence: int,
    predicate: TimelineEvidencePredicate,
    phase: Literal["snapshot", "stream"],
    limit: int,
    replay_order: bool,
) -> Any:
    """Build the one bounded SQL read used for snapshots and sequence replay."""
    if limit < 1 or limit > MAX_TIMELINE_EVENTS + 1:
        raise ValueError("timeline ledger internal limit is invalid")
    ledger = TimelineLedgerEvent.__table__
    conditions: list[Any] = [
        ledger.c.workspace_id == read_scope.workspace_id,
        ledger.c.sequence > after_sequence,
        ledger.c.sequence <= through_sequence,
        timeline_evidence_sql_predicate(ledger, predicate, phase=phase),
    ]
    order_by = (ledger.c.sequence.asc(),) if replay_order else (ledger.c.sequence.desc(),)
    return select(ledger).where(and_(*conditions)).order_by(*order_by).limit(limit)


def _timeline_overview_buckets_statement(
    read_scope: TimelineLedgerReadScope,
    *,
    predicate: TimelineEvidencePredicate,
    bucket_width_ms: int,
) -> Any:
    """Aggregate the exact snapshot selection without returning event payloads."""
    ledger = TimelineLedgerEvent.__table__
    bucket_index = _timeline_overview_bucket_index(ledger, predicate, bucket_width_ms)
    problem = ledger.c.activity.in_(("warning", "unhealthy"))
    return (
        select(
            bucket_index.label("bucket_index"),
            func.count().label("event_count"),
            func.count().filter(problem).label("problem_count"),
        )
        .where(
            and_(
                ledger.c.workspace_id == read_scope.workspace_id,
                timeline_evidence_sql_predicate(ledger, predicate, phase="snapshot"),
            )
        )
        .group_by(bucket_index)
        .order_by(bucket_index.asc())
    )


def _timeline_overview_activity_facets_statement(
    read_scope: TimelineLedgerReadScope,
    *,
    predicate: TimelineEvidencePredicate,
) -> Any:
    ledger = TimelineLedgerEvent.__table__
    return (
        select(ledger.c.activity.label("activity"), func.count().label("count"))
        .where(
            and_(
                ledger.c.workspace_id == read_scope.workspace_id,
                timeline_evidence_sql_predicate(ledger, predicate, phase="snapshot"),
            )
        )
        .group_by(ledger.c.activity)
        .order_by(ledger.c.activity.asc())
    )


def _timeline_overview_kind_facets_statement(
    read_scope: TimelineLedgerReadScope,
    *,
    predicate: TimelineEvidencePredicate,
) -> Any:
    ledger = TimelineLedgerEvent.__table__
    kind = timeline_resource_kind_sql(ledger)
    return (
        select(kind.label("kind"), func.count().label("count"))
        .where(
            and_(
                ledger.c.workspace_id == read_scope.workspace_id,
                timeline_evidence_sql_predicate(ledger, predicate, phase="snapshot"),
                kind.is_not(None),
            )
        )
        .group_by(kind)
        .order_by(func.count().desc(), kind.asc())
    )


def _timeline_overview_later_count_statement(
    read_scope: TimelineLedgerReadScope,
    *,
    predicate: TimelineEvidencePredicate,
) -> Any:
    """Count frozen-window suffix facts with the same grants and filters."""
    ledger = TimelineLedgerEvent.__table__
    return select(func.count()).where(
        and_(
            ledger.c.workspace_id == read_scope.workspace_id,
            timeline_evidence_sql_predicate(ledger, predicate, phase="stream"),
        )
    )


def _timeline_overview_bucket_index(
    ledger: Any,
    predicate: TimelineEvidencePredicate,
    bucket_width_ms: int,
) -> Any:
    from_ms = literal(
        predicate.replay_identity.window.from_ms,
        type_=BigInteger(),
    )
    elapsed_ms = func.extract("epoch", ledger.c.occurred_at) * 1_000 - from_ms
    return cast(func.floor(elapsed_ms / bucket_width_ms), Integer)


def _overview_counts(rows: tuple[Any, ...], *, key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row[key]
        if not isinstance(value, str) or not value.strip():
            continue
        count = int(row["count"])
        if count < 0:
            raise ValueError("timeline overview count cannot be negative")
        counts[value] = count
    return counts


async def fanout_committed_timeline_append(
    append: TimelineLedgerAppend,
    fanout: TimelineEventFanout | None = None,
) -> None:
    """Announce only from an outer transaction's after-commit callback.

    ``append_timeline_event`` may participate in a larger unit of work, so this
    function intentionally accepts an already committed append result instead
    of publishing from inside repository persistence.
    """
    if append.inserted and fanout is not None:
        await fanout.publish_committed(append)


def replay_result(
    *,
    after_sequence: int,
    retained_from_sequence: int,
    high_water_sequence: int,
    events: Iterable[TimelineLedgerRecord | TimelineEvent | tuple[int, TimelineEvent]],
) -> TimelineReplayResult:
    """Pure replay folding used by storage adapters and deterministic contract tests."""
    if after_sequence < 0 or retained_from_sequence < 1 or high_water_sequence < 0:
        raise ValueError("timeline replay bounds are invalid")
    if after_sequence < retained_from_sequence - 1:
        return TimelineReplayResult(
            status="resync_required",
            records=(),
            high_water_sequence=high_water_sequence,
            retained_from_sequence=retained_from_sequence,
            reason="retention_boundary",
        )
    ordered: list[TimelineLedgerRecord] = []
    for index, item in enumerate(events, start=1):
        if isinstance(item, TimelineLedgerRecord):
            record = item
        elif isinstance(item, tuple):
            record = TimelineLedgerRecord(sequence=item[0], event=item[1])
        else:
            record = TimelineLedgerRecord(sequence=index, event=item)
        if record.sequence > after_sequence:
            ordered.append(record)
    deduped: list[TimelineLedgerRecord] = []
    seen_source_keys: set[str] = set()
    for record in sorted(ordered, key=lambda item: item.sequence):
        if record.event.source_key not in seen_source_keys:
            seen_source_keys.add(record.event.source_key)
            deduped.append(record)
    return TimelineReplayResult(
        status="available",
        records=tuple(deduped),
        high_water_sequence=high_water_sequence,
        retained_from_sequence=retained_from_sequence,
    )


def _event_values(event: TimelineEvent, *, sequence: int) -> dict[str, object]:
    return {
        "workspace_id": event.scope.workspace_id,
        "sequence": sequence,
        "source_key": event.source_key,
        "event_id": event.event_id,
        "source": event.source,
        "native_id": event.native_id,
        "activity": event.activity,
        "cluster_id": event.scope.cluster_id,
        "namespace": _event_namespace(event),
        "freshness": event.scope.freshness,
        "event_type": event.event_type,
        "severity": event.severity,
        "title": event.title,
        "subject": event.subject.model_dump(mode="json"),
        "resource": event.resource.model_dump(mode="json") if event.resource else None,
        "owner": event.owner.model_dump(mode="json") if event.owner else None,
        "metadata": event.metadata,
        "occurred_at": event.occurred_at,
    }


def _event_namespace(event: TimelineEvent) -> str | None:
    if event.resource is not None:
        return event.resource.namespace
    subject = event.subject
    return getattr(subject, "namespace", None)


def _event_from_row(row: Any) -> TimelineEvent:
    return TimelineEvent.model_validate(
        {
            "event_id": row["event_id"],
            "source": row["source"],
            "source_key": row["source_key"],
            "native_id": row["native_id"],
            "activity": row["activity"],
            "occurred_at": row["occurred_at"],
            "scope": {
                "workspace_id": row["workspace_id"],
                "cluster_id": row["cluster_id"],
                "freshness": row["freshness"],
            },
            "subject": row["subject"],
            "resource": row["resource"],
            "event_type": row["event_type"],
            "severity": row["severity"],
            "title": row["title"],
            "owner": row["owner"],
            "metadata": row["metadata"],
        }
    )


def _record_from_row(row: Any) -> TimelineLedgerRecord:
    return TimelineLedgerRecord(sequence=int(row["sequence"]), event=_event_from_row(row))


def _validate_requested_limit(limit: int) -> None:
    if isinstance(limit, bool) or limit < 1 or limit > MAX_TIMELINE_EVENTS:
        raise ValueError(f"timeline ledger limit must be between 1 and {MAX_TIMELINE_EVENTS}")
