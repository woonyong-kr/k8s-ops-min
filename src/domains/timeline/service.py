"""Server-side Timeline query resolution.

This layer turns a browser request into an observed, source-authorized ledger
boundary.  It deliberately owns neither HTTP serialization nor SQL: keeping
those concerns separate makes the same resolution usable by retained snapshots
and the later resumable SSE reader.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from domains.target.connectivity import cluster_connection_status
from domains.timeline.access import (
    AuthorizedTimelineScope,
    require_timeline_capability_access,
    require_timeline_cluster_ids,
    resolve_authorized_timeline_scope,
)
from domains.timeline.cursor import TimelineCursorBinding
from domains.timeline.pins import timeline_pin_membership, visible_timeline_pin_set
from domains.timeline.predicate import TimelineEvidencePredicate
from domains.timeline.repository import TimelineLedgerReadScope
from domains.timeline.settings import (
    timeline_capability_descriptor,
    timeline_control_selection_is_valid,
    timeline_max_window_ms,
    timeline_query_bounds,
    timeline_realtime_policy,
)
from packages.contracts.parity import ClusterScope, Freshness
from packages.contracts.timeline import (
    RealtimePolicy,
    TimelineCapabilityDescriptor,
    TimelinePinApplicationTarget,
    TimelinePinMutation,
    TimelinePinnedApplicationSubject,
    TimelinePinnedResourceSubject,
    TimelinePinResourceTarget,
    TimelinePinSet,
    TimelinePinTarget,
    TimelinePinUpsertRequest,
    TimelineQuery,
    TimelineQueryBounds,
)

INVALID_WINDOW_DETAIL = "timeline window exceeds the server read limit"
BEFORE_RETAINED_HISTORY_DETAIL = "timeline window starts before retained history"
FUTURE_WINDOW_DETAIL = "timeline window ends after server time"
INVALID_CONTROL_SELECTION_DETAIL = "timeline control selection is unavailable"
SCOPE_NOT_FOUND_DETAIL = "timeline scope not found"
FRESHNESS_UNAVAILABLE_DETAIL = "timeline freshness is unavailable"
PINS_UNAVAILABLE_DETAIL = "timeline pins are unavailable"
PIN_TARGET_NOT_FOUND_DETAIL = "timeline pin target not found"


@dataclass(frozen=True)
class TimelineReadResolution:
    """One immutable read boundary shared by snapshot and replay transports."""

    authorized: AuthorizedTimelineScope
    # Query identity intentionally excludes collection freshness.  The latter
    # is observed output and must never influence a replay cursor binding.
    query: TimelineQuery
    scopes: tuple[ClusterScope, ...]
    read_scope: TimelineLedgerReadScope
    evidence_predicate: TimelineEvidencePredicate
    cursor_binding: TimelineCursorBinding
    policy: RealtimePolicy
    capabilities: TimelineCapabilityDescriptor
    pin_set: TimelinePinSet | None = None


async def resolve_timeline_capabilities(
    db: Any,
    current: Any,
) -> TimelineCapabilityDescriptor:
    """Return the one server-owned descriptor without creating a read session.

    No query, freshness observation, evidence predicate, replay cursor, or
    subscription is necessary for this first-render bootstrap read.  It still
    resolves all source-specific workspace grants before exposing metadata.
    """
    authorized = await resolve_authorized_timeline_scope(db, current)
    require_timeline_capability_access(authorized)
    return timeline_capability_descriptor()


async def resolve_timeline_read(
    db: Any,
    current: Any,
    requested_query: TimelineQuery,
    *,
    enforce_server_time_bounds: bool = False,
) -> TimelineReadResolution:
    """Authorize one query and derive freshness only from persisted agent state."""
    authorized = await resolve_authorized_timeline_scope(db, current)
    requested_workspace_id = requested_query.scopes[0].workspace_id
    if requested_workspace_id != authorized.workspace_id:
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)

    requested_cluster_ids = {scope.cluster_id for scope in requested_query.scopes}
    require_timeline_cluster_ids(authorized, requested_cluster_ids)
    _validate_window(requested_query)
    _validate_control_selection(requested_query)
    query_bounds = timeline_query_bounds()
    if enforce_server_time_bounds:
        _validate_server_time_bounds(requested_query, query_bounds)
    scopes = await _observed_scopes(db, authorized.workspace_id, requested_query.scopes)
    read_scope = TimelineLedgerReadScope(
        workspace_id=authorized.workspace_id,
        scopes=scopes,
        inventory_cluster_ids=authorized.cluster_ids & requested_cluster_ids,
        kubernetes_event_cluster_ids=authorized.cluster_ids & requested_cluster_ids,
        incident_cluster_ids=authorized.incident_cluster_ids & requested_cluster_ids,
        application_workflow_ids=authorized.deployment_application_ids,
        gitops_application_ids=authorized.application_ids,
    )
    pin_set = (
        await read_visible_timeline_pin_set(db, authorized)
        if requested_query.filters.pinned_only
        else None
    )
    pin_membership = None if pin_set is None else timeline_pin_membership(pin_set)
    evidence_predicate = TimelineEvidencePredicate.from_query(
        read_scope,
        requested_query,
        pin_membership=pin_membership,
    )
    binding = TimelineCursorBinding.from_query(
        user_id=authorized.user_id,
        authorization_revision=authorized.authorization_revision,
        query=requested_query,
        pin_set_revision=None if pin_set is None else pin_set.revision,
    )
    return TimelineReadResolution(
        authorized=authorized,
        query=requested_query,
        scopes=scopes,
        read_scope=read_scope,
        evidence_predicate=evidence_predicate,
        cursor_binding=binding,
        pin_set=pin_set,
        policy=timeline_realtime_policy(),
        capabilities=timeline_capability_descriptor(query_bounds=query_bounds),
    )


def _validate_window(query: TimelineQuery) -> None:
    if query.window.to_ms - query.window.from_ms > timeline_max_window_ms():
        raise HTTPException(status_code=422, detail=INVALID_WINDOW_DETAIL)


def _validate_server_time_bounds(query: TimelineQuery, bounds: TimelineQueryBounds) -> None:
    """Reject unavailable strip windows before an aggregate can look silently empty.

    Authorization runs before this check so a caller cannot use a malformed
    range to learn about a hidden workspace or cluster.  The same retained
    limits apply to live and frozen presentation modes; mode never grants a
    browser permission to substitute its own clock.
    """
    if query.window.from_ms < bounds.earliest_queryable_ms:
        raise HTTPException(status_code=422, detail=BEFORE_RETAINED_HISTORY_DETAIL)
    if query.window.to_ms > bounds.server_now_ms:
        raise HTTPException(status_code=422, detail=FUTURE_WINDOW_DETAIL)


def _validate_control_selection(query: TimelineQuery) -> None:
    """Reject controls that are absent or unavailable in this deployment."""
    if not timeline_control_selection_is_valid(query):
        raise HTTPException(status_code=422, detail=INVALID_CONTROL_SELECTION_DETAIL)


async def read_timeline_pins(db: Any, current: Any) -> TimelinePinSet:
    """Read current-user/workspace pins only, projecting revoked rows away from the response."""
    authorized = await resolve_authorized_timeline_scope(db, current)
    return await read_visible_timeline_pin_set(db, authorized)


async def read_visible_timeline_pin_set(
    db: Any,
    authorized: AuthorizedTimelineScope,
) -> TimelinePinSet:
    reader = getattr(db, "read_timeline_pin_set", None)
    if not callable(reader):
        raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
    try:
        pin_set = await asyncio.to_thread(
            reader,
            authorized.workspace_id,
            authorized.user_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL) from exc
    if not isinstance(pin_set, TimelinePinSet):
        raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
    return visible_timeline_pin_set(pin_set, authorized)


async def put_timeline_pin(
    db: Any,
    current: Any,
    request: TimelinePinUpsertRequest,
) -> TimelinePinMutation:
    """Add only a currently readable target after server-side identity materialization."""
    authorized = await resolve_authorized_timeline_scope(db, current)
    subject = await _resolve_pin_target(db, authorized, request.target)
    writer = getattr(db, "put_timeline_pin", None)
    if not callable(writer):
        raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
    try:
        mutation = await asyncio.to_thread(
            writer,
            workspace_id=authorized.workspace_id,
            user_id=authorized.user_id,
            expected_revision=request.expected_revision,
            subject=subject,
        )
    except ValueError:
        raise
    if not isinstance(mutation, TimelinePinMutation):
        raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
    return mutation.model_copy(
        update={"pin_set": visible_timeline_pin_set(mutation.pin_set, authorized)}
    )


async def delete_timeline_pin(
    db: Any,
    current: Any,
    *,
    pin_id: str,
    expected_revision: int,
) -> TimelinePinMutation:
    """Idempotently remove an owner's pin even when it is currently hidden by revoked access."""
    authorized = await resolve_authorized_timeline_scope(db, current)
    writer = getattr(db, "delete_timeline_pin", None)
    if not callable(writer):
        raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
    try:
        mutation = await asyncio.to_thread(
            writer,
            workspace_id=authorized.workspace_id,
            user_id=authorized.user_id,
            pin_id=pin_id,
            expected_revision=expected_revision,
        )
    except ValueError:
        raise
    if not isinstance(mutation, TimelinePinMutation):
        raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
    return mutation.model_copy(
        update={"pin_set": visible_timeline_pin_set(mutation.pin_set, authorized)}
    )


async def _resolve_pin_target(
    db: Any,
    authorized: AuthorizedTimelineScope,
    target: TimelinePinTarget,
) -> TimelinePinnedResourceSubject | TimelinePinnedApplicationSubject:
    if isinstance(target, TimelinePinResourceTarget):
        if (
            target.scope.workspace_id != authorized.workspace_id
            or target.scope.cluster_id not in authorized.cluster_ids
        ):
            raise HTTPException(status_code=404, detail=PIN_TARGET_NOT_FOUND_DETAIL)
        resolver = getattr(db, "resolve_timeline_pin_resource", None)
        if not callable(resolver):
            raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
        resolved = await asyncio.to_thread(
            resolver,
            workspace_id=authorized.workspace_id,
            cluster_id=target.scope.cluster_id,
            uid=target.resource.uid,
        )
        if (
            not isinstance(resolved, TimelinePinnedResourceSubject)
            or resolved.resource != target.resource
        ):
            raise HTTPException(status_code=404, detail=PIN_TARGET_NOT_FOUND_DETAIL)
        return resolved
    if isinstance(target, TimelinePinApplicationTarget):
        if target.application_id not in authorized.application_ids:
            raise HTTPException(status_code=404, detail=PIN_TARGET_NOT_FOUND_DETAIL)
        resolver = getattr(db, "resolve_timeline_pin_application", None)
        if not callable(resolver):
            raise HTTPException(status_code=503, detail=PINS_UNAVAILABLE_DETAIL)
        resolved = await asyncio.to_thread(
            resolver,
            workspace_id=authorized.workspace_id,
            application_id=target.application_id,
        )
        if (
            not isinstance(resolved, TimelinePinnedApplicationSubject)
            or resolved.application_id != target.application_id
        ):
            raise HTTPException(status_code=404, detail=PIN_TARGET_NOT_FOUND_DETAIL)
        return resolved
    raise HTTPException(status_code=404, detail=PIN_TARGET_NOT_FOUND_DETAIL)


async def _observed_scopes(
    db: Any,
    workspace_id: str,
    requested_scopes: tuple[ClusterScope, ...],
) -> tuple[ClusterScope, ...]:
    getter = getattr(db, "latest_cluster_agent_statuses", None)
    if not callable(getter):
        raise HTTPException(status_code=503, detail=FRESHNESS_UNAVAILABLE_DETAIL)
    cluster_ids = {scope.cluster_id for scope in requested_scopes}
    try:
        statuses = await asyncio.to_thread(getter, workspace_id, cluster_ids)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=FRESHNESS_UNAVAILABLE_DETAIL) from exc
    if not isinstance(statuses, Mapping):
        raise HTTPException(status_code=503, detail=FRESHNESS_UNAVAILABLE_DETAIL)
    return tuple(
        scope.model_copy(update={"freshness": _observed_freshness(statuses.get(scope.cluster_id))})
        for scope in requested_scopes
    )


def _observed_freshness(agent: object) -> Freshness:
    status = cluster_connection_status(agent if isinstance(agent, Mapping) else None)
    if status == "online":
        return "live"
    if status == "stale":
        return "stale"
    return "disconnected"
