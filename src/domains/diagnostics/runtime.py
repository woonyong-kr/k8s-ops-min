"""Authenticated, bounded runtime observations assembled from existing repositories."""

from __future__ import annotations

import asyncio
import os
import platform
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from domains.identity.dependencies import resolve_allowed_cluster_ids
from domains.target.connectivity import cluster_connection_status
from packages.contracts.bootstrap import (
    AGENT_DIAGNOSTICS_LIMIT,
    CAPABILITY_LIMIT,
    CONSUMER_LAG_LIMIT,
    EVENT_STATUS_LIMIT,
    AgentCollectionDiagnostics,
    AgentDiagnosticsItem,
    AgentInventorySnapshotDiagnostics,
    ConsumerLagDiagnostics,
    EventPipelineDiagnostics,
    EventStatusCount,
    RuntimeDiagnosticsResponse,
    RuntimeProcessSnapshot,
    TimelineDiagnostics,
)
from packages.contracts.identity import Permission

_PROCESS_STARTED_AT = time.monotonic()


async def collect_runtime_diagnostics(
    db: Any,
    current: Any,
) -> RuntimeDiagnosticsResponse:
    """Collect independent sections concurrently and preserve healthy sections on failure."""
    observed_at = datetime.now(UTC)
    event_pipeline, timeline, agent_collection = await asyncio.gather(
        asyncio.to_thread(_collect_event_pipeline, db),
        asyncio.to_thread(_collect_timeline, db, str(current.workspace_id)),
        _collect_agents(db, current),
    )
    reason_codes = sorted(
        {
            *event_pipeline.reason_codes,
            *timeline.reason_codes,
            *agent_collection.reason_codes,
        }
    )
    return RuntimeDiagnosticsResponse(
        observed_at=observed_at,
        completeness="partial" if reason_codes else "complete",
        runtime=_runtime_process_snapshot(),
        event_pipeline=event_pipeline,
        timeline=timeline,
        agent_collection=agent_collection,
        reason_codes=reason_codes,
    )


def _runtime_process_snapshot() -> RuntimeProcessSnapshot:
    return RuntimeProcessSnapshot(
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        process_id=os.getpid(),
        cpu_count=os.cpu_count(),
        thread_count=threading.active_count(),
        uptime_seconds=max(0.0, time.monotonic() - _PROCESS_STARTED_AT),
    )


def _collect_event_pipeline(db: Any) -> EventPipelineDiagnostics:
    try:
        statuses = sorted(
            (
                EventStatusCount(status=str(status), count=max(0, int(count)))
                for status, count in db.event_processing_status_counts().items()
            ),
            key=lambda item: item.status,
        )
        lag, lagging_count = _consumer_lag_diagnostics(db)
        reason_codes: list[str] = []
        if len(statuses) > EVENT_STATUS_LIMIT:
            reason_codes.append("event_status_limit_reached")
        if lagging_count > CONSUMER_LAG_LIMIT:
            reason_codes.append("consumer_lag_sample_truncated")
        return EventPipelineDiagnostics(
            availability="partial" if reason_codes else "available",
            open_dead_letters=max(0, int(db.open_dead_letter_count())),
            outbox_pending=max(0, int(db.outbox_pending_count())),
            processing_statuses=statuses[:EVENT_STATUS_LIMIT],
            consumer_lag=lag,
            reason_codes=reason_codes,
        )
    except Exception:
        return EventPipelineDiagnostics(
            availability="unavailable",
            reason_codes=["event_pipeline_unavailable"],
        )


def _consumer_lag_diagnostics(db: Any) -> tuple[list[ConsumerLagDiagnostics], int]:
    snapshot_reader = getattr(db, "event_consumer_lag_snapshot", None)
    if callable(snapshot_reader):
        snapshot = snapshot_reader(limit=CONSUMER_LAG_LIMIT)
        return (
            [
                ConsumerLagDiagnostics(
                    consumer=str(sample.durable),
                    subject=str(sample.subject),
                    pending=max(0, int(sample.pending)),
                    ack_pending=max(0, int(sample.ack_pending)),
                    redelivered=max(0, int(sample.redelivered)),
                )
                for sample in snapshot.samples
            ],
            max(0, int(snapshot.lagging_count)),
        )

    pending = db.event_consumer_pending_by_consumer_subject()
    ack_pending = db.event_consumer_ack_pending_by_consumer_subject()
    redelivered = db.event_consumer_redelivered_by_consumer_subject()
    lagging_keys = [
        key
        for key in set(pending) | set(ack_pending) | set(redelivered)
        if any(
            max(0, int(values.get(key, 0))) > 0 for values in (pending, ack_pending, redelivered)
        )
    ]
    lagging_keys.sort(
        key=lambda key: (
            -max(0, int(pending.get(key, 0))),
            -max(0, int(ack_pending.get(key, 0))),
            -max(0, int(redelivered.get(key, 0))),
            str(key[0]),
            str(key[1]),
        )
    )
    return (
        [
            ConsumerLagDiagnostics(
                consumer=str(key[0]),
                subject=str(key[1]),
                pending=max(0, int(pending.get(key, 0))),
                ack_pending=max(0, int(ack_pending.get(key, 0))),
                redelivered=max(0, int(redelivered.get(key, 0))),
            )
            for key in lagging_keys[:CONSUMER_LAG_LIMIT]
        ],
        len(lagging_keys),
    )


def _collect_timeline(db: Any, workspace_id: str) -> TimelineDiagnostics:
    try:
        observation = db.timeline_diagnostics(workspace_id)
        if not isinstance(observation, Mapping):
            raise TypeError("timeline diagnostics must be a mapping")
        return TimelineDiagnostics(
            availability="available",
            event_count=observation.get("event_count"),
            oldest_occurred_at=observation.get("oldest_occurred_at"),
            newest_occurred_at=observation.get("newest_occurred_at"),
            high_water_sequence=observation.get("high_water_sequence"),
            retained_from_sequence=observation.get("retained_from_sequence"),
        )
    except Exception:
        return TimelineDiagnostics(
            availability="unavailable",
            reason_codes=["timeline_unavailable"],
        )


async def _collect_agents(db: Any, current: Any) -> AgentCollectionDiagnostics:
    try:
        workspace_id = str(current.workspace_id)
        selected, truncated = await asyncio.to_thread(
            _select_agent_registrations,
            db,
            current,
            workspace_id,
        )
        selected_ids = {
            str(item.get("cluster_id") or "").strip()
            for item in selected
            if str(item.get("cluster_id") or "").strip()
        }
        if selected_ids:
            statuses, snapshots = await asyncio.gather(
                asyncio.to_thread(
                    db.latest_cluster_agent_statuses,
                    workspace_id,
                    selected_ids,
                ),
                asyncio.to_thread(
                    db.latest_inventory_snapshots,
                    workspace_id,
                    selected_ids,
                ),
            )
        else:
            statuses, snapshots = {}, {}
        items: list[AgentDiagnosticsItem] = []
        reason_codes: list[str] = []
        if truncated:
            reason_codes.append("agent_collection_limit_reached")
        for registration in selected:
            try:
                item, capability_truncated = _agent_item(registration, statuses, snapshots)
                items.append(item)
                if capability_truncated:
                    reason_codes.append("agent_capability_limit_reached")
            except (TypeError, ValueError):
                reason_codes.append("agent_observation_invalid")
        reason_codes = sorted(set(reason_codes))
        return AgentCollectionDiagnostics(
            availability="partial" if reason_codes else "available",
            items=items,
            reason_codes=reason_codes,
        )
    except Exception:
        return AgentCollectionDiagnostics(
            availability="unavailable",
            reason_codes=["agent_collection_unavailable"],
        )


def _select_agent_registrations(
    db: Any,
    current: Any,
    workspace_id: str,
) -> tuple[list[Mapping[str, Any]], bool]:
    """Authorize first, then materialize one bounded registration page."""
    allowed_ids = resolve_allowed_cluster_ids(
        db,
        current,
        workspace_id,
        Permission.CLUSTER_READ.value,
    )
    registrations = db.list_cluster_registrations(
        workspace_id,
        cluster_ids=allowed_ids,
        limit=AGENT_DIAGNOSTICS_LIMIT + 1,
    )
    ordered = sorted(
        (item for item in registrations if isinstance(item, Mapping)),
        key=lambda item: str(item.get("cluster_id") or ""),
    )
    return ordered[:AGENT_DIAGNOSTICS_LIMIT], len(ordered) > AGENT_DIAGNOSTICS_LIMIT


def _agent_item(
    registration: Mapping[str, Any],
    statuses: Mapping[str, Mapping[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> tuple[AgentDiagnosticsItem, bool]:
    cluster_id = str(registration.get("cluster_id") or "").strip()
    if not cluster_id:
        raise ValueError("cluster id is required")
    agent = statuses.get(cluster_id)
    snapshot = snapshots.get(cluster_id)
    raw_capabilities = agent.get("capabilities", []) if agent else []
    capabilities = sorted(
        {
            capability.strip()
            for capability in raw_capabilities
            if isinstance(capability, str) and capability.strip()
        }
    )
    latest_inventory = None
    if snapshot is not None:
        latest_inventory = AgentInventorySnapshotDiagnostics(
            status=str(snapshot.get("status") or ""),
            source=str(snapshot.get("source") or ""),
            collected_at=snapshot.get("collected_at"),
            resource_count=max(0, int(snapshot.get("resource_count") or 0)),
        )
    return (
        AgentDiagnosticsItem(
            cluster_id=cluster_id,
            name=str(registration.get("name") or cluster_id),
            environment=str(registration.get("environment") or ""),
            registration_status=str(registration.get("status") or "unknown"),
            connection_status=cluster_connection_status(agent),
            agent_id=str(agent.get("agent_id")) if agent and agent.get("agent_id") else None,
            agent_status=str(agent.get("status")) if agent and agent.get("status") else None,
            last_seen_at=agent.get("last_seen_at") if agent else None,
            capabilities=capabilities[:CAPABILITY_LIMIT],
            latest_inventory=latest_inventory,
        ),
        len(capabilities) > CAPABILITY_LIMIT,
    )
