"""Traffic projection over persisted outbound-Agent evidence windows."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from packages.config.refresh_policies import integral_refresh_after_seconds
from packages.contracts.parity import ClusterScope
from packages.contracts.traffic.observations import (
    MAX_TRAFFIC_PAGE_SIZE,
    MAX_TRAFFIC_TOTAL_COUNT,
    TRAFFIC_CARETTA_FLOW_METRIC,
    TRAFFIC_HUBBLE_FLOW_METRIC,
    TRAFFIC_ISTIO_FLOW_METRIC,
    TRAFFIC_SINCE_SECONDS,
    TrafficEndpoint,
    TrafficFlowFacets,
    TrafficObservationStatus,
    TrafficObservationSummary,
    TrafficObservedObservationStatus,
    TrafficObservedObservationSummary,
    TrafficObservedRelationships,
    TrafficOverviewResponse,
    TrafficProtocol,
    TrafficProtocolFacet,
    TrafficRelationship,
    TrafficRelationships,
    TrafficScopeCoverage,
    TrafficSince,
    TrafficSort,
    TrafficSortOrder,
    TrafficVerdict,
    TrafficVerdictFacet,
)

TRAFFIC_OBSERVATION_UNAVAILABLE = "traffic_observation_not_integrated"
TRAFFIC_EVIDENCE_WINDOW_UNAVAILABLE = "traffic_evidence_window_unavailable"
TRAFFIC_EVIDENCE_STALE = "traffic_evidence_stale"
TRAFFIC_ACTIVE_SOURCE_UNAVAILABLE = "traffic_active_source_evidence_unavailable"
TRAFFIC_SOURCE_SELECTION_UNOBSERVED = "traffic_source_selection_unobserved"
TRAFFIC_EVIDENCE_INVALID = "traffic_evidence_invalid"

TRAFFIC_QUERY_BY_SOURCE = {
    "caretta": TRAFFIC_CARETTA_FLOW_METRIC,
    "hubble": TRAFFIC_HUBBLE_FLOW_METRIC,
    "istio": TRAFFIC_ISTIO_FLOW_METRIC,
}
TRAFFIC_SOURCE_BY_QUERY = {query: source for source, query in TRAFFIC_QUERY_BY_SOURCE.items()}


def traffic_overview(
    *,
    workspace_id: str,
    contexts: Mapping[str, Mapping[str, Any]],
    namespace_refs: Iterable[tuple[str, str]],
    selected_cluster_ids: Iterable[str],
    evidence_windows: Iterable[Mapping[str, Any]] = (),
    agent_statuses: Mapping[str, Mapping[str, Any]] | None = None,
    since: TrafficSince = "5m",
    protocols: Iterable[TrafficProtocol] = (),
    verdicts: Iterable[TrafficVerdict] = (),
    sort: TrafficSort = "connections",
    order: TrafficSortOrder = "desc",
    offset: int = 0,
    limit: int = 50,
    next_cursor: str | None = None,
    now: datetime | None = None,
) -> TrafficOverviewResponse:
    """Build one authorized flow page without direct cluster or Prometheus reads."""

    selected = tuple(sorted({_text(value) for value in selected_cluster_ids if _text(value)}))
    normalized_namespace_refs = tuple(sorted(set(namespace_refs)))
    namespaces = _namespaces_by_cluster(normalized_namespace_refs)
    coverage = traffic_scope_coverage(
        workspace_id=workspace_id,
        contexts=contexts,
        namespaces=namespaces,
        selected_cluster_ids=selected,
    )
    reasons: set[str] = set(coverage.reason_codes)
    effective_now = _utc(now or datetime.now(UTC))
    latest_windows = _latest_windows_by_cluster(evidence_windows, set(selected))
    edges: list[TrafficRelationship] = []
    observed_clusters: set[str] = set()
    source_keys: set[str] = set()
    observed_at: list[datetime] = []
    for cluster_id in selected:
        row = latest_windows.get(cluster_id)
        if row is None:
            reasons.add(f"{TRAFFIC_EVIDENCE_WINDOW_UNAVAILABLE}:{cluster_id}")
            continue
        stamp = _window_time(row)
        if stamp is None:
            reasons.add(f"{TRAFFIC_EVIDENCE_INVALID}:{cluster_id}")
            continue
        if effective_now - stamp > timedelta(seconds=TRAFFIC_SINCE_SECONDS[since]):
            reasons.add(f"{TRAFFIC_EVIDENCE_STALE}:{cluster_id}")
            continue
        results = _metric_results(row)
        active_source = _active_source((agent_statuses or {}).get(cluster_id))
        selected_results = _selected_results(
            cluster_id=cluster_id,
            active_source=active_source,
            results=results,
            reasons=reasons,
        )
        if not selected_results:
            continue
        observed_clusters.add(cluster_id)
        observed_at.append(stamp)
        for source_key, raw_result in selected_results:
            source_keys.add(source_key)
            projected, invalid = _flow_samples(
                cluster_id=cluster_id,
                source_key=source_key,
                result=raw_result,
                observed_at=stamp.isoformat(),
            )
            edges.extend(projected)
            if invalid:
                reasons.add(f"{TRAFFIC_EVIDENCE_INVALID}:{cluster_id}:{source_key}")

    if not observed_clusters:
        unavailable_reasons = tuple(sorted(reasons)) or (TRAFFIC_OBSERVATION_UNAVAILABLE,)
        return TrafficOverviewResponse(
            scope_coverage=coverage,
            observation=TrafficObservationStatus(reason_codes=unavailable_reasons),
            summary=TrafficObservationSummary(reason_codes=unavailable_reasons),
            relationships=TrafficRelationships(reason_codes=unavailable_reasons),
            refresh_after_seconds=integral_refresh_after_seconds("metrics_prometheus"),
        )

    reasons.update(coverage.reason_codes)
    scope_filtered = _filtered_edges(
        edges,
        namespaces=namespaces,
        namespace_filter_active=bool(normalized_namespace_refs),
        protocols=set(),
        verdicts=set(),
    )
    filtered = _filtered_edges(
        scope_filtered,
        namespaces={},
        namespace_filter_active=False,
        protocols=set(protocols),
        verdicts=set(verdicts),
    )
    ordered = _sorted_edges(filtered, sort=sort, order=order)
    bounded_offset = max(0, offset)
    bounded_limit = min(MAX_TRAFFIC_PAGE_SIZE, max(1, limit))
    page = tuple(ordered[bounded_offset : bounded_offset + bounded_limit])
    has_more = bounded_offset + len(page) < len(ordered)
    if has_more and next_cursor is None:
        raise ValueError("traffic page requires a next cursor")
    availability = (
        "partial"
        if reasons
        or len(observed_clusters) != len(selected)
        or coverage.availability != "available"
        else "available"
    )
    observed_reason_codes = tuple(sorted(reasons))
    latest_observed = max(observed_at).isoformat()
    return TrafficOverviewResponse(
        scope_coverage=coverage,
        observation=TrafficObservedObservationStatus(
            availability=availability,
            observed_at=latest_observed,
            since=since,
            source_keys=tuple(sorted(source_keys)),
            reason_codes=observed_reason_codes,
        ),
        summary=TrafficObservedObservationSummary(
            availability=availability,
            total_flow_count=len(ordered),
            denied_flow_count=sum(edge.verdict in {"dropped", "error"} for edge in ordered),
            external_flow_count=sum(
                _is_external(edge.source) or _is_external(edge.target) for edge in ordered
            ),
            reason_codes=observed_reason_codes,
        ),
        relationships=TrafficObservedRelationships(
            availability=availability,
            edges=page,
            total_count=len(ordered),
            has_more=has_more,
            next_cursor=next_cursor if has_more else None,
            facets=_traffic_facets(scope_filtered),
            reason_codes=observed_reason_codes,
        ),
        refresh_after_seconds=integral_refresh_after_seconds("metrics_prometheus"),
    )


def traffic_scope_coverage(
    *,
    workspace_id: str,
    contexts: Mapping[str, Mapping[str, Any]],
    namespaces: Mapping[str, tuple[str, ...]],
    selected_cluster_ids: Iterable[str],
) -> TrafficScopeCoverage:
    """Describe inventory freshness per authorized selected cluster."""

    selected = tuple(sorted({_text(value) for value in selected_cluster_ids if _text(value)}))
    if not selected:
        return TrafficScopeCoverage(
            availability="unavailable",
            reason_codes=("authorization_scope_empty",),
        )
    reasons: set[str] = set()
    observed_at: list[str] = []
    scopes: list[ClusterScope] = []
    has_unavailable = False
    has_partial = False
    for cluster_id in selected:
        context = contexts.get(cluster_id)
        if context is None or int(context.get("snapshot_revision") or 0) <= 0:
            has_unavailable = True
            reasons.add(f"inventory_snapshot_unavailable:{cluster_id}")
        elif not bool(context.get("resources_complete")) or not bool(
            context.get("labels_complete")
        ):
            has_partial = True
            reasons.add(f"inventory_snapshot_incomplete:{cluster_id}")
        for reason in (context or {}).get("partial_reason_codes", ()):
            if normalized := _text(reason):
                has_partial = True
                reasons.add(normalized)
        if stamp := _optional_text((context or {}).get("observed_at")):
            observed_at.append(stamp)
        scopes.append(
            ClusterScope(
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                namespaces=namespaces.get(cluster_id, ()),
                freshness=_freshness(context),
            )
        )
    availability = "unavailable" if has_unavailable else "partial" if has_partial else "available"
    return TrafficScopeCoverage(
        availability=availability,
        scopes=tuple(scopes),
        observed_at=max(observed_at) if observed_at else None,
        reason_codes=tuple(sorted(reasons)),
    )


def _selected_results(
    *,
    cluster_id: str,
    active_source: str | None,
    results: Mapping[str, Any],
    reasons: set[str],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    if active_source:
        query_name = TRAFFIC_QUERY_BY_SOURCE.get(active_source)
        result = results.get(query_name) if query_name else None
        if not isinstance(result, Mapping):
            reasons.add(f"{TRAFFIC_ACTIVE_SOURCE_UNAVAILABLE}:{cluster_id}:{active_source}")
            return ()
        return ((active_source, result),)
    observed = tuple(
        (TRAFFIC_SOURCE_BY_QUERY[query_name], result)
        for query_name, result in sorted(results.items())
        if query_name in TRAFFIC_SOURCE_BY_QUERY and isinstance(result, Mapping)
    )
    if observed:
        reasons.add(f"{TRAFFIC_SOURCE_SELECTION_UNOBSERVED}:{cluster_id}")
    else:
        reasons.add(f"{TRAFFIC_EVIDENCE_WINDOW_UNAVAILABLE}:{cluster_id}")
    return observed


def _flow_samples(
    *,
    cluster_id: str,
    source_key: str,
    result: Mapping[str, Any],
    observed_at: str,
) -> tuple[list[TrafficRelationship], bool]:
    samples = result.get("samples")
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
        return [], True
    projected: dict[str, TrafficRelationship] = {}
    invalid = False
    for sample in samples:
        if not isinstance(sample, Mapping):
            invalid = True
            continue
        edge = _flow_sample(
            cluster_id=cluster_id,
            source_key=source_key,
            sample=sample,
            observed_at=observed_at,
        )
        if edge is None:
            invalid = True
            continue
        previous = projected.get(edge.flow_id)
        if previous is None or edge.connections >= previous.connections:
            projected[edge.flow_id] = edge
    return list(projected.values()), invalid


def _flow_sample(
    *,
    cluster_id: str,
    source_key: str,
    sample: Mapping[str, Any],
    observed_at: str,
) -> TrafficRelationship | None:
    labels = sample.get("metric")
    if not isinstance(labels, Mapping):
        return None
    value = _non_negative_number(sample.get("value"))
    if value is None:
        return None
    if source_key == "caretta":
        source = _endpoint(
            cluster_id=cluster_id,
            name=_first_text(labels, "client_name", "source"),
            namespace=_first_text(labels, "client_namespace", "source_namespace"),
            kind=_first_text(labels, "client_kind", "source_kind"),
        )
        target = _endpoint(
            cluster_id=cluster_id,
            name=_first_text(labels, "server_name", "destination"),
            namespace=_first_text(labels, "server_namespace", "destination_namespace"),
            kind=_first_text(labels, "server_kind", "destination_kind"),
            service=_first_text(labels, "server_service"),
        )
        protocol: TrafficProtocol = "tcp"
        verdict: TrafficVerdict = "forwarded"
        port = _port(labels.get("server_port"))
    elif source_key == "istio":
        source = _endpoint(
            cluster_id=cluster_id,
            name=_first_text(labels, "source_workload", "source"),
            namespace=_first_text(labels, "source_workload_namespace", "source_namespace"),
            kind="Workload",
            workload=_first_text(labels, "source_workload"),
        )
        target = _endpoint(
            cluster_id=cluster_id,
            name=_first_text(
                labels,
                "destination_workload",
                "destination_service_name",
                "destination",
            ),
            namespace=_first_text(
                labels,
                "destination_workload_namespace",
                "destination_service_namespace",
                "destination_namespace",
            ),
            kind="Workload",
            workload=_first_text(labels, "destination_workload"),
            service=_first_text(labels, "destination_service_name"),
        )
        protocol = _protocol(_first_text(labels, "request_protocol", "protocol"))
        response_code = _first_text(labels, "response_code")
        verdict = "error" if response_code.startswith("5") else "forwarded"
        port = _port(labels.get("destination_port"))
    elif source_key == "hubble":
        source = _endpoint(
            cluster_id=cluster_id,
            name=_first_text(labels, "source", "source_name", "source_workload"),
            namespace=_first_text(labels, "source_namespace", "source_ns"),
            kind=_first_text(labels, "source_kind") or "Workload",
            workload=_first_text(labels, "source_workload"),
        )
        target = _endpoint(
            cluster_id=cluster_id,
            name=_first_text(labels, "destination", "destination_name", "destination_workload"),
            namespace=_first_text(labels, "destination_namespace", "destination_ns"),
            kind=_first_text(labels, "destination_kind") or "Workload",
            workload=_first_text(labels, "destination_workload"),
        )
        protocol = _protocol(_first_text(labels, "protocol", "l7_protocol"))
        verdict = _verdict(_first_text(labels, "verdict"))
        port = _port(labels.get("destination_port"))
    else:
        return None
    if source is None or target is None:
        return None
    connections = _bounded_integer(value, minimum_one=value > 0)
    identity = "\0".join(
        (
            cluster_id,
            source_key,
            source.namespace or "",
            source.name,
            target.namespace or "",
            target.name,
            protocol,
            str(port or 0),
            verdict,
        )
    )
    return TrafficRelationship(
        flow_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        source_key=source_key,
        source=source,
        target=target,
        protocol=protocol,
        port=port,
        verdict=verdict,
        connections=connections,
        observed_at=_sample_time(sample) or observed_at,
    )


def _endpoint(
    *,
    cluster_id: str,
    name: str,
    namespace: str = "",
    kind: str = "",
    workload: str = "",
    service: str = "",
) -> TrafficEndpoint | None:
    normalized_name = _text(name) or _text(service)
    if not normalized_name:
        return None
    normalized_namespace = _optional_text(namespace)
    normalized_kind = _text(kind) or ("Workload" if normalized_namespace else "External")
    return TrafficEndpoint(
        cluster_id=cluster_id,
        name=normalized_name,
        namespace=normalized_namespace,
        kind=normalized_kind,
        workload=_optional_text(workload),
        service=_optional_text(service),
    )


def _filtered_edges(
    edges: Iterable[TrafficRelationship],
    *,
    namespaces: Mapping[str, tuple[str, ...]],
    namespace_filter_active: bool,
    protocols: set[TrafficProtocol],
    verdicts: set[TrafficVerdict],
) -> list[TrafficRelationship]:
    deduplicated: dict[str, TrafficRelationship] = {}
    for edge in edges:
        if namespace_filter_active:
            allowed = set(namespaces.get(edge.source.cluster_id, ()))
            if not allowed or not (
                edge.source.namespace in allowed or edge.target.namespace in allowed
            ):
                continue
        if protocols and edge.protocol not in protocols:
            continue
        if verdicts and edge.verdict not in verdicts:
            continue
        previous = deduplicated.get(edge.flow_id)
        if previous is None or edge.observed_at >= previous.observed_at:
            deduplicated[edge.flow_id] = edge
    return list(deduplicated.values())


def _sorted_edges(
    edges: Iterable[TrafficRelationship],
    *,
    sort: TrafficSort,
    order: TrafficSortOrder,
) -> list[TrafficRelationship]:
    def key(edge: TrafficRelationship) -> tuple[object, str]:
        if sort == "last_seen":
            primary: object = edge.observed_at
        elif sort == "source":
            primary = (edge.source.cluster_id, edge.source.namespace or "", edge.source.name)
        elif sort == "destination":
            primary = (edge.target.cluster_id, edge.target.namespace or "", edge.target.name)
        else:
            primary = edge.connections
        return primary, edge.flow_id

    return sorted(edges, key=key, reverse=order == "desc")


def _traffic_facets(edges: Iterable[TrafficRelationship]) -> TrafficFlowFacets:
    protocol_counts: dict[TrafficProtocol, int] = {}
    verdict_counts: dict[TrafficVerdict, int] = {}
    for edge in edges:
        protocol_counts[edge.protocol] = protocol_counts.get(edge.protocol, 0) + 1
        verdict_counts[edge.verdict] = verdict_counts.get(edge.verdict, 0) + 1
    return TrafficFlowFacets(
        protocols=tuple(
            TrafficProtocolFacet(value=value, count=count)
            for value, count in sorted(protocol_counts.items())
        ),
        verdicts=tuple(
            TrafficVerdictFacet(value=value, count=count)
            for value, count in sorted(verdict_counts.items())
        ),
    )


def _latest_windows_by_cluster(
    values: Iterable[Mapping[str, Any]],
    selected: set[str],
) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for value in values:
        cluster_id = _text(value.get("cluster_id"))
        if cluster_id not in selected:
            continue
        current = latest.get(cluster_id)
        if current is None or (_window_time(value) or datetime.min.replace(tzinfo=UTC)) > (
            _window_time(current) or datetime.min.replace(tzinfo=UTC)
        ):
            latest[cluster_id] = value
    return latest


def _metric_results(row: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = row.get("payload")
    body = payload if isinstance(payload, Mapping) else row
    metrics = body.get("metrics")
    if not isinstance(metrics, Mapping):
        return {}
    results = metrics.get("results")
    return results if isinstance(results, Mapping) else {}


def _active_source(status: Mapping[str, Any] | None) -> str | None:
    details = status.get("details") if isinstance(status, Mapping) else None
    traffic_sources = details.get("traffic_sources") if isinstance(details, Mapping) else None
    return _optional_text(
        traffic_sources.get("active_source") if isinstance(traffic_sources, Mapping) else None
    )


def _window_time(row: Mapping[str, Any]) -> datetime | None:
    payload = row.get("payload")
    body = payload if isinstance(payload, Mapping) else {}
    for value in (
        body.get("window_start"),
        row.get("window_start"),
        row.get("updated_at"),
        row.get("created_at"),
    ):
        if parsed := _parse_time(value):
            return parsed
    return None


def _sample_time(sample: Mapping[str, Any]) -> str | None:
    value = sample.get("timestamp")
    number = _non_negative_number(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _freshness(context: Mapping[str, Any] | None) -> str:
    if context is None or int(context.get("snapshot_revision") or 0) <= 0:
        return "disconnected"
    if not bool(context.get("resources_complete")) or not bool(context.get("labels_complete")):
        return "partial"
    return "live"


def _namespaces_by_cluster(
    namespace_refs: Iterable[tuple[str, str]],
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, set[str]] = {}
    for cluster_id, namespace in namespace_refs:
        if normalized_cluster := _text(cluster_id):
            if normalized_namespace := _text(namespace):
                grouped.setdefault(normalized_cluster, set()).add(normalized_namespace)
    return {cluster_id: tuple(sorted(values)) for cluster_id, values in grouped.items()}


def _protocol(value: object) -> TrafficProtocol:
    normalized = _text(value).casefold()
    if "grpc" in normalized:
        return "grpc"
    if "http" in normalized:
        return "http"
    if "dns" in normalized:
        return "dns"
    if normalized in {"tcp", "udp"}:
        return normalized  # type: ignore[return-value]
    return "unknown"


def _verdict(value: object) -> TrafficVerdict:
    normalized = _text(value).casefold()
    if normalized in {"forwarded", "allowed", "allow", "ok"}:
        return "forwarded"
    if normalized in {"dropped", "denied", "deny", "drop"}:
        return "dropped"
    if normalized in {"error", "failed", "failure"}:
        return "error"
    return "unknown"


def _port(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 65_535 else None


def _first_text(values: Mapping[str, Any], *keys: str) -> str:
    return next((_text(values.get(key)) for key in keys if _text(values.get(key))), "")


def _non_negative_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _bounded_integer(value: float, *, minimum_one: bool = False) -> int:
    rounded = int(round(value))
    if minimum_one:
        rounded = max(1, rounded)
    return min(MAX_TRAFFIC_TOTAL_COUNT, max(0, rounded))


def _is_external(endpoint: TrafficEndpoint) -> bool:
    return endpoint.namespace is None or endpoint.kind.casefold() == "external"


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_text(value: object) -> str | None:
    return _text(value) or None
