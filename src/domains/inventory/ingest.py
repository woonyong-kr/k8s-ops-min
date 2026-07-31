"""One transaction boundary for every inventory snapshot producer."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.exc import OperationalError

from domains.inventory.repository import InventorySnapshotMutation
from domains.timeline.repository import (
    TimelineEventFanout,
    TimelineLedgerAppend,
    fanout_committed_timeline_append,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import has_active_connection, unit_of_work_or_null

InventorySnapshotAfterPersist = Callable[[JsonObject], Awaitable[None]]
InventorySnapshotBeforePersist = Callable[[], Awaitable[None]]


async def ingest_inventory_snapshot(
    *,
    db: Any,
    workspace_id: str,
    cluster_id: str,
    agent_id: str,
    payload: JsonObject,
    fanout: TimelineEventFanout | object | None = None,
    ready_fanout: object | None = None,
    before_persist: InventorySnapshotBeforePersist | None = None,
    after_persist: InventorySnapshotAfterPersist | None = None,
) -> JsonObject:
    """Persist, ledger-append, and only then announce one inventory snapshot.

    This orchestration deliberately owns the outer unit of work. A caller that already
    holds a database transaction cannot know when it commits, so publishing from that
    nested boundary would make a rolled-back fact observable on the live stream.
    """
    if has_active_connection():
        raise RuntimeError("inventory ingest must own the outer unit of work")

    if before_persist is None and after_persist is None:
        result, appends = await asyncio.to_thread(
            _persist_inventory_snapshot,
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            agent_id=agent_id,
            payload=payload,
        )
    else:
        result, appends = await _persist_inventory_snapshot_with_callbacks(
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            agent_id=agent_id,
            payload=payload,
            before_persist=before_persist,
            after_persist=after_persist,
        )

    publisher = fanout if callable(getattr(fanout, "publish_committed", None)) else None
    for append in appends:
        await fanout_committed_timeline_append(append, publisher)
    ready_publisher = getattr(ready_fanout, "publish_committed", None)
    if result.get("accepted") is True and callable(ready_publisher):
        await ready_publisher(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            snapshot_id=str(result["snapshot_id"]),
        )
    return result


def _persist_inventory_snapshot(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    agent_id: str,
    payload: JsonObject,
) -> tuple[JsonObject, tuple[TimelineLedgerAppend, ...]]:
    """Persist one callback-free agent snapshot on a worker thread.

    Evidence-job ingestion has no asynchronous transactional dependencies. Its
    Kubernetes snapshot can be large, so keeping the whole synchronous unit of
    work on the ASGI loop would stall every agent and browser request in the
    process until the inventory and Timeline writes commit.

    Cluster-scoped writers share one transaction-scoped advisory lock. Under
    frequent collection that lock can exceed ``lock_timeout`` and raise
    ``LockNotAvailable``. Retrying on a fresh transaction avoids dropping the
    snapshot; each attempt re-runs stale detection, so ordering stays correct.
    """

    def _once() -> tuple[JsonObject, tuple[TimelineLedgerAppend, ...]]:
        with unit_of_work_or_null(db):
            return _write_inventory_snapshot(
                db,
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                agent_id=agent_id,
                payload=payload,
            )

    return _run_with_advisory_lock_retry(_once)


_ADVISORY_LOCK_RETRY_ATTEMPTS = 5
_ADVISORY_LOCK_RETRY_BASE_DELAY_SECONDS = 0.05
_LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"


def _run_with_advisory_lock_retry(
    operation: Callable[[], tuple[JsonObject, tuple[TimelineLedgerAppend, ...]]],
) -> tuple[JsonObject, tuple[TimelineLedgerAppend, ...]]:
    """Retry one snapshot write when the shared advisory lock times out."""
    for attempt in range(_ADVISORY_LOCK_RETRY_ATTEMPTS):
        try:
            return operation()
        except OperationalError as exc:
            last = attempt == _ADVISORY_LOCK_RETRY_ATTEMPTS - 1
            if last or not _is_lock_timeout(exc):
                raise
            time.sleep(_ADVISORY_LOCK_RETRY_BASE_DELAY_SECONDS * (2**attempt))
    raise RuntimeError("unreachable advisory lock retry")


def _is_lock_timeout(exc: OperationalError) -> bool:
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate == _LOCK_NOT_AVAILABLE_SQLSTATE:
        return True
    message = str(original if original is not None else exc).lower()
    return "lock timeout" in message or "locknotavailable" in message


async def _persist_inventory_snapshot_with_callbacks(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    agent_id: str,
    payload: JsonObject,
    before_persist: InventorySnapshotBeforePersist | None,
    after_persist: InventorySnapshotAfterPersist | None,
) -> tuple[JsonObject, tuple[TimelineLedgerAppend, ...]]:
    """Preserve callback/UoW affinity for the two transactional producers."""
    with unit_of_work_or_null(db):
        if before_persist is not None:
            await before_persist()
        result, appends = _write_inventory_snapshot(
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            agent_id=agent_id,
            payload=payload,
        )
        if after_persist is not None:
            await after_persist(result)
    return result, appends


def _write_inventory_snapshot(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    agent_id: str,
    payload: JsonObject,
) -> tuple[JsonObject, tuple[TimelineLedgerAppend, ...]]:
    mutation_writer = getattr(db, "save_inventory_snapshot_mutation", None)
    if callable(mutation_writer):
        mutation = mutation_writer(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            agent_id=agent_id,
            payload=payload,
        )
        if not isinstance(mutation, InventorySnapshotMutation):
            raise TypeError("inventory snapshot mutation writer returned an invalid result")
        return mutation.result, append_inventory_timeline_events(db, mutation)
    # Small contract-test stores predate the Timeline ledger. Production Database
    # always provides the mutation writer; preserve the public response shape.
    return (
        db.save_inventory_snapshot(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            agent_id=agent_id,
            payload=payload,
        ),
        (),
    )


def append_inventory_timeline_events(
    db: Any,
    mutation: InventorySnapshotMutation,
) -> tuple[TimelineLedgerAppend, ...]:
    """Append immutable facts within the caller-owned inventory transaction."""
    if not mutation.timeline_events:
        return ()
    append_many = getattr(db, "append_timeline_events", None)
    if callable(append_many):
        return tuple(append_many(mutation.timeline_events))
    append = getattr(db, "append_timeline_event", None)
    if not callable(append):
        raise RuntimeError("inventory timeline ledger append is unavailable")
    return tuple(append(event) for event in mutation.timeline_events)
