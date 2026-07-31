"""Durable Timeline coverage from isolated Kubernetes Event capture evidence.

The inventory snapshot is the durable authority for capture completeness.  A
``TimelineCoverage`` is emitted only after an explicitly incomplete global
capture is followed by a separately proven global recovery.  Open gaps do not
have an observed end bound, so they are deliberately absent rather than
clipped to a request window or to the current clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from domains.inventory.kubernetes_events import (
    KubernetesEventCapture,
    exact_nonnegative_int,
    normalized_text,
    positive_int,
)
from packages.contracts.parity import ClusterScope
from packages.contracts.timeline import TimelineCoverage, TimelineQuery, TimelineWindow


class TimelineCoverageLimitExceeded(ValueError):
    """The retained coverage response cannot be represented within its server limit."""


@dataclass(frozen=True)
class KubernetesEventCaptureObservation:
    """One normalized global Event capture state from a durable snapshot."""

    cluster_id: str
    observed_at: datetime
    state: str


def project_kubernetes_event_capture_coverage(
    read_scope: Any,
    *,
    window: TimelineWindow,
    snapshots: Iterable[Mapping[str, object]],
    snapshots_ordered: bool = False,
    max_intervals: int | None = None,
) -> tuple[TimelineCoverage, ...]:
    """Project closed, proven global Event capture gaps for one authorized window.

    A coverage interval retains its observed failure/recovery bounds even when
    it only overlaps the requested window.  Clipping would create a boundary
    that was never observed by the collector.
    """
    scopes_by_cluster = _authorized_scopes_by_cluster(read_scope)
    if snapshots_ordered:
        return _project_ordered_capture_coverage(
            scopes_by_cluster,
            window=window,
            snapshots=snapshots,
            max_intervals=max_intervals,
        )
    observations_by_cluster: dict[str, list[KubernetesEventCaptureObservation]] = {}
    for snapshot in snapshots:
        observation = _capture_observation(snapshot, scopes_by_cluster, window=window)
        if observation is not None:
            observations_by_cluster.setdefault(observation.cluster_id, []).append(observation)

    projected: list[TimelineCoverage] = []
    for cluster_id, observations in observations_by_cluster.items():
        opened_at: datetime | None = None
        for observation in sorted(observations, key=lambda item: item.observed_at):
            if observation.state == "gap":
                opened_at = opened_at or observation.observed_at
                continue
            if opened_at is None or observation.observed_at <= opened_at:
                continue
            from_ms = _milliseconds(opened_at)
            to_ms = _milliseconds(observation.observed_at)
            if _intersects_window(from_ms, to_ms, window):
                _append_coverage_intervals(
                    projected,
                    scopes=scopes_by_cluster[cluster_id],
                    from_ms=from_ms,
                    to_ms=to_ms,
                    max_intervals=max_intervals,
                )
            opened_at = None
    return tuple(sorted(projected, key=_coverage_sort_key))


def _project_ordered_capture_coverage(
    scopes_by_cluster: Mapping[str, tuple[ClusterScope, ...]],
    *,
    window: TimelineWindow,
    snapshots: Iterable[Mapping[str, object]],
    max_intervals: int | None,
) -> tuple[TimelineCoverage, ...]:
    """Project an already cluster/time ordered DB cursor with O(scopes + output) memory."""
    opened_by_cluster: dict[str, datetime] = {}
    last_observed_by_cluster: dict[str, datetime] = {}
    projected: list[TimelineCoverage] = []
    for snapshot in snapshots:
        observation = _capture_observation(snapshot, scopes_by_cluster, window=window)
        if observation is None:
            continue
        previous_observed_at = last_observed_by_cluster.get(observation.cluster_id)
        if previous_observed_at is not None and observation.observed_at < previous_observed_at:
            raise ValueError("timeline coverage snapshots must be ordered per cluster")
        last_observed_by_cluster[observation.cluster_id] = observation.observed_at
        if observation.state == "gap":
            opened_by_cluster.setdefault(observation.cluster_id, observation.observed_at)
            continue
        opened_at = opened_by_cluster.get(observation.cluster_id)
        if opened_at is None or observation.observed_at <= opened_at:
            continue
        from_ms = _milliseconds(opened_at)
        to_ms = _milliseconds(observation.observed_at)
        if _intersects_window(from_ms, to_ms, window):
            _append_coverage_intervals(
                projected,
                scopes=scopes_by_cluster[observation.cluster_id],
                from_ms=from_ms,
                to_ms=to_ms,
                max_intervals=max_intervals,
            )
        del opened_by_cluster[observation.cluster_id]
    return tuple(sorted(projected, key=_coverage_sort_key))


def _append_coverage_intervals(
    projected: list[TimelineCoverage],
    *,
    scopes: tuple[ClusterScope, ...],
    from_ms: int,
    to_ms: int,
    max_intervals: int | None,
) -> None:
    for scope in scopes:
        if max_intervals is not None and len(projected) >= max_intervals:
            raise TimelineCoverageLimitExceeded(
                f"timeline coverage exceeds the server interval limit ({max_intervals})"
            )
        projected.append(
            TimelineCoverage(
                scope=scope,
                source="kubernetes_event",
                from_ms=from_ms,
                to_ms=to_ms,
                reason="collection_gap",
            )
        )


def authorized_kubernetes_event_coverage(
    read_scope: Any,
    *,
    window: TimelineWindow,
    coverage: Iterable[TimelineCoverage],
) -> tuple[TimelineCoverage, ...]:
    """Defence-in-depth filter for repository output before HTTP serialization."""
    scopes_by_cluster = _authorized_scopes_by_cluster(read_scope)
    allowed_scope_keys = {
        _scope_key(scope) for scopes in scopes_by_cluster.values() for scope in scopes
    }
    accepted = {
        _coverage_key(item): item
        for item in coverage
        if isinstance(item, TimelineCoverage)
        and item.source == "kubernetes_event"
        and item.reason == "collection_gap"
        and _scope_key(item.scope) in allowed_scope_keys
        and _intersects_window(item.from_ms, item.to_ms, window)
    }
    return tuple(sorted(accepted.values(), key=_coverage_sort_key))


def coverage_additions(
    previous: Iterable[TimelineCoverage],
    current: Iterable[TimelineCoverage],
) -> tuple[TimelineCoverage, ...]:
    """Return monotonic additions only; a pruned read must not fabricate a resolution."""
    known = {_coverage_key(item) for item in previous if isinstance(item, TimelineCoverage)}
    additions = {
        _coverage_key(item): item
        for item in current
        if isinstance(item, TimelineCoverage) and _coverage_key(item) not in known
    }
    return tuple(sorted(additions.values(), key=_coverage_sort_key))


def kubernetes_event_coverage_visible_for_query(query: TimelineQuery) -> bool:
    """Avoid a source coverage warning when query filters hide that source entirely."""
    filters = query.filters
    if filters.activity and "k8s_event" not in filters.activity:
        return False
    if filters.kinds and "Event" not in filters.kinds:
        return False
    # Coverage has no Event text and must not claim that it matches a search term.
    return not filters.query.strip()


def _authorized_scopes_by_cluster(read_scope: Any) -> dict[str, tuple[ClusterScope, ...]]:
    authorized_cluster_ids = getattr(read_scope, "kubernetes_event_cluster_ids", frozenset())
    scopes = getattr(read_scope, "scopes", ())
    result: dict[str, list[ClusterScope]] = {}
    for scope in scopes:
        if isinstance(scope, ClusterScope) and scope.cluster_id in authorized_cluster_ids:
            result.setdefault(scope.cluster_id, []).append(scope)
    return {
        cluster_id: tuple(sorted(cluster_scopes, key=_scope_key))
        for cluster_id, cluster_scopes in result.items()
    }


def _capture_observation(
    snapshot: Mapping[str, object],
    scopes_by_cluster: Mapping[str, tuple[ClusterScope, ...]],
    *,
    window: TimelineWindow,
) -> KubernetesEventCaptureObservation | None:
    if normalized_text(snapshot.get("status")) == "ignored_stale":
        return None
    cluster_id = normalized_text(snapshot.get("cluster_id"))
    if cluster_id not in scopes_by_cluster:
        return None
    summary = snapshot.get("summary")
    source_summary = summary.get("summary") if isinstance(summary, Mapping) else None
    if not isinstance(source_summary, Mapping):
        return None
    capture = KubernetesEventCapture.from_snapshot_summary(source_summary)
    if capture.observed_at is None or _milliseconds(capture.observed_at) >= window.to_ms:
        return None
    if _is_explicit_gap(capture):
        return KubernetesEventCaptureObservation(
            cluster_id=cluster_id,
            observed_at=capture.observed_at,
            state="gap",
        )
    if _is_proven_recovery(capture):
        return KubernetesEventCaptureObservation(
            cluster_id=cluster_id,
            observed_at=capture.observed_at,
            state="recovery",
        )
    return None


def _is_explicit_gap(capture: KubernetesEventCapture) -> bool:
    gap = normalized_text(capture.coverage.get("gap"))
    return (
        not capture.complete
        and bool(gap)
        and capture.reason == gap
        and capture.coverage.get("scope") == "all_namespaces"
        and capture.coverage.get("pagination") == "continue"
    )


def _is_proven_recovery(capture: KubernetesEventCapture) -> bool:
    return (
        capture.authoritative
        and positive_int(capture.coverage.get("page_count")) is not None
        and exact_nonnegative_int(capture.coverage.get("event_count")) is not None
        and bool(normalized_text(capture.coverage.get("resource_version")))
    )


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def _intersects_window(from_ms: int, to_ms: int, window: TimelineWindow) -> bool:
    return from_ms < window.to_ms and window.from_ms < to_ms


def _scope_key(scope: ClusterScope) -> tuple[str, str, tuple[str, ...]]:
    return scope.workspace_id, scope.cluster_id, scope.namespaces


def _coverage_key(item: TimelineCoverage) -> tuple[object, ...]:
    return (
        *_scope_key(item.scope),
        item.source,
        item.from_ms,
        item.to_ms,
        item.reason,
    )


def _coverage_sort_key(item: TimelineCoverage) -> tuple[object, ...]:
    return item.from_ms, item.to_ms, *_scope_key(item.scope), item.source, item.reason
