"""Safe workload-detail projection from the persisted inventory read model."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from domains.inventory.snapshot_evidence import snapshot_source_summary
from domains.log_stream.service import WORKLOAD_KIND_NAMES
from domains.target.connectivity import (
    AGENT_STATUS_NEVER_CONNECTED,
    AGENT_STATUS_ONLINE,
    AGENT_STATUS_STALE,
    cluster_connection_status,
)
from domains.workload_detail.rightsizing_projection import workload_rightsizing_evidence
from packages.contracts.parity import CapabilitySet, ClusterScope, ResourceRef
from packages.contracts.rightsizing import RightsizingWorkloadEvidence
from packages.contracts.workload_detail import (
    WorkloadDetail,
    WorkloadEventCollection,
    WorkloadEventObservation,
    WorkloadFeatureAvailability,
    WorkloadLabel,
    WorkloadLogStreamCapability,
    WorkloadObservation,
    WorkloadObservationCoverage,
    WorkloadPodCollection,
    WorkloadPodObservation,
    WorkloadReplicaObservation,
)

WORKLOAD_RESOURCE_TYPE = "workload"
RELATIONSHIP_LIMIT = 100
EVENT_LIMIT = 50


class WorkloadDetailNotFound(LookupError):
    """The requested resource is absent or does not match the exact API identity."""


class WorkloadDetailUnavailable(RuntimeError):
    """The server cannot establish the inventory observation boundary."""


class WorkloadDetailIdentityUnavailable(RuntimeError):
    """No Kubernetes UID is available, so an exact ResourceRef cannot be issued."""


def workload_detail_projection(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    api_group: str,
    api_version: str,
    kind: str,
    namespace: str | None,
    name: str,
) -> WorkloadDetail:
    """Build one read-only detail response without crossing the raw boundary."""

    expected_api_version = canonical_api_version(api_group, api_version)
    resource_reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(resource_reader):
        raise WorkloadDetailUnavailable("exact inventory identity reader is unavailable")
    resource = resource_reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=WORKLOAD_RESOURCE_TYPE,
        api_version=expected_api_version,
        kind=kind,
        namespace=namespace,
        name=name,
    )
    if not isinstance(resource, Mapping) or not same_api_version(
        resource.get("api_version"), expected_api_version
    ):
        raise WorkloadDetailNotFound()
    resource = dict(resource)
    resource_ref = resource_ref_from(resource)

    latest_reader = getattr(db, "latest_inventory_snapshot", None)
    if not callable(latest_reader):
        raise WorkloadDetailUnavailable("inventory snapshot metadata is unavailable")
    latest_snapshot = latest_reader(workspace_id, cluster_id)
    if not isinstance(latest_snapshot, Mapping):
        raise WorkloadDetailUnavailable("inventory snapshot metadata is unavailable")
    latest_snapshot = dict(latest_snapshot)
    latest_snapshot_id = required_text(latest_snapshot.get("snapshot_id"))
    observation_snapshot_id = required_text(resource.get("snapshot_id"))
    if not latest_snapshot_id or not observation_snapshot_id:
        raise WorkloadDetailUnavailable("inventory snapshot identity is unavailable")

    source_summary = snapshot_source_summary(latest_snapshot) or {}
    source_truncated = nested_mapping(source_summary, "collection_limits").get("truncated") is True
    source_complete = source_summary.get("resources_complete") is True and not source_truncated
    coverage_reasons: list[str] = []
    if source_truncated:
        coverage_reasons.append("source_resources_truncated")
    elif not source_complete:
        coverage_reasons.append("source_resources_incomplete")
    if observation_snapshot_id != latest_snapshot_id:
        coverage_reasons.append("resource_not_observed_in_latest_snapshot")
    coverage_availability = "available" if not coverage_reasons else "partial"

    connection, liveness_reason = cluster_liveness(db, workspace_id, cluster_id)
    freshness = (
        "live"
        if connection == AGENT_STATUS_ONLINE and coverage_availability == "available"
        else "partial"
        if connection == AGENT_STATUS_ONLINE
        else "stale"
        if connection == AGENT_STATUS_STALE
        else "disconnected"
        if connection == AGENT_STATUS_NEVER_CONNECTED
        else "partial"
    )
    if liveness_reason:
        coverage_reasons.append(liveness_reason)
        if coverage_availability == "available":
            coverage_availability = "partial"

    scope = ClusterScope(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespaces=(namespace,) if namespace else (),
        freshness=freshness,
    )
    coverage = WorkloadObservationCoverage(
        availability=coverage_availability,
        observation_snapshot_id=observation_snapshot_id,
        latest_snapshot_id=latest_snapshot_id,
        observed_at=text_or_none(resource.get("observed_at")),
        reason_codes=tuple(unique(coverage_reasons)),
    )

    related_reader = getattr(db, "list_related_inventory_resources", None)
    event_reader = getattr(db, "list_resource_events", None)
    if not callable(related_reader) or not callable(event_reader):
        raise WorkloadDetailUnavailable("inventory detail readers are unavailable")
    related = related_reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource=resource,
        limit=RELATIONSHIP_LIMIT,
    )
    pods, pod_excluded = project_pods(
        related.get("pods", ()) if isinstance(related, Mapping) else ()
    )
    event_rows = event_reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource=resource,
        limit=EVENT_LIMIT,
    )
    events, event_excluded = project_events(event_rows)
    log_stream = log_stream_capability(resource, connection)
    rightsizing = workload_rightsizing_evidence(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource=resource_ref,
    )

    return WorkloadDetail(
        scope=scope,
        observation=WorkloadObservation(
            resource=resource_ref,
            health=safe_health(resource.get("health")),
            replicas=replicas_from(resource),
            labels=labels_from(resource),
            observed_at=text_or_none(resource.get("observed_at")),
        ),
        coverage=coverage,
        pods=WorkloadPodCollection(
            availability="partial",
            items=tuple(pods),
            excluded_count=pod_excluded,
            reason_codes=("direct_pod_relationship_is_bounded",),
        ),
        events=WorkloadEventCollection(
            availability="partial",
            items=tuple(events),
            excluded_count=event_excluded,
            reason_codes=("direct_event_relationship_is_bounded",),
        ),
        log_stream=log_stream,
        rightsizing=rightsizing,
        capabilities=CapabilitySet(
            scope=scope,
            resource=resource_ref,
            revision=observation_snapshot_id,
            actions=(),
        ),
        features=feature_availability(
            coverage_availability,
            log_stream,
            rightsizing,
            resource,
        ),
    )


def canonical_api_version(api_group: str, api_version: str) -> str:
    group = api_group.strip()
    version = api_version.strip()
    if group != api_group or version != api_version or not version or "/" in version:
        raise ValueError("api identity is invalid")
    if any(character.isspace() for character in group) or any(
        character.isspace() for character in version
    ):
        raise ValueError("api identity is invalid")
    return f"{group}/{version}" if group else version


def same_api_version(value: object, expected: str) -> bool:
    return isinstance(value, str) and value.strip() == expected


def resource_ref_from(resource: Mapping[str, Any]) -> ResourceRef:
    api_version = required_text(resource.get("api_version"))
    group, version = split_api_version(api_version)
    uid = required_text(resource.get("uid"))
    kind = required_text(resource.get("kind"))
    name = required_text(resource.get("name"))
    if not uid:
        raise WorkloadDetailIdentityUnavailable("workload uid is unavailable")
    if not kind or not name or not version:
        raise WorkloadDetailUnavailable("resource identity is unavailable")
    namespace = text_or_none(resource.get("namespace"))
    return ResourceRef(
        api_group=group,
        version=version,
        kind=kind,
        namespace=namespace,
        name=name,
        uid=uid,
    )


def split_api_version(value: str) -> tuple[str, str]:
    if "/" not in value:
        return "", value
    group, version = value.rsplit("/", 1)
    return group, version


def replicas_from(resource: Mapping[str, Any]) -> WorkloadReplicaObservation:
    summary = mapping(resource.get("summary"))
    return WorkloadReplicaObservation(
        desired=nonnegative_int(summary.get("desired_replicas")),
        ready=nonnegative_int(summary.get("ready_replicas")),
        available=nonnegative_int(summary.get("available_replicas")),
        updated=nonnegative_int(summary.get("updated_replicas")),
        unavailable=nonnegative_int(summary.get("unavailable_replicas")),
    )


def labels_from(resource: Mapping[str, Any]) -> tuple[WorkloadLabel, ...]:
    labels = mapping(resource.get("labels"))
    return tuple(
        WorkloadLabel(key=key, value=value)
        for key, value in sorted(labels.items())
        if isinstance(key, str) and key and isinstance(value, str)
    )


def project_pods(rows: Iterable[object]) -> tuple[list[WorkloadPodObservation], int]:
    projected: list[WorkloadPodObservation] = []
    excluded = 0
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("resource_type") or "").lower() != "pod":
            excluded += 1
            continue
        try:
            projected.append(
                WorkloadPodObservation(
                    resource=resource_ref_from(row),
                    health=safe_health(row.get("health")),
                    observed_at=text_or_none(row.get("observed_at")),
                )
            )
        except (WorkloadDetailIdentityUnavailable, WorkloadDetailUnavailable):
            excluded += 1
    return projected, excluded


def project_events(rows: Iterable[object]) -> tuple[list[WorkloadEventObservation], int]:
    projected: list[WorkloadEventObservation] = []
    excluded = 0
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("resource_type") or "").lower() != "event":
            excluded += 1
            continue
        try:
            summary = mapping(row.get("summary"))
            projected.append(
                WorkloadEventObservation(
                    resource=resource_ref_from(row),
                    event_type=text_or_none(summary.get("type")),
                    reason=text_or_none(summary.get("reason")),
                    occurrence_count=nonnegative_int(summary.get("count")),
                    last_occurred_at=text_or_none(summary.get("last_occurrence_at")),
                )
            )
        except (WorkloadDetailIdentityUnavailable, WorkloadDetailUnavailable):
            excluded += 1
    return projected, excluded


def log_stream_capability(
    resource: Mapping[str, Any], connection: str | None
) -> WorkloadLogStreamCapability:
    if connection != AGENT_STATUS_ONLINE:
        return WorkloadLogStreamCapability(
            availability="unavailable",
            reason_codes=("cluster_not_live",),
        )
    if not text_or_none(resource.get("namespace")):
        return WorkloadLogStreamCapability(
            availability="unavailable",
            reason_codes=("cluster_scoped_workload_logs_not_supported",),
        )
    kind = required_text(resource.get("kind")).lower()
    for stream_kind, kubernetes_kind in WORKLOAD_KIND_NAMES.items():
        if kubernetes_kind.lower() == kind:
            return WorkloadLogStreamCapability(availability="available", stream_kind=stream_kind)
    return WorkloadLogStreamCapability(
        availability="unavailable",
        reason_codes=("workload_log_stream_not_supported",),
    )


def feature_availability(
    coverage: str,
    log_stream: WorkloadLogStreamCapability,
    rightsizing: RightsizingWorkloadEvidence,
    resource: Mapping[str, Any],
) -> tuple[WorkloadFeatureAvailability, ...]:
    covered = "available" if coverage == "available" else "partial"
    covered_reasons = () if covered == "available" else ("inventory_coverage_partial",)
    unavailable = (
        ("metrics", "workload_metrics_not_integrated"),
        ("topology", "workload_topology_not_integrated"),
        ("timeline", "workload_timeline_not_integrated"),
        ("rbac", "workload_rbac_not_integrated"),
        ("gitops", "workload_gitops_not_integrated"),
        ("helm", "workload_helm_not_integrated"),
        ("operations", "workload_operations_not_integrated"),
        ("yaml", "safe_manifest_projection_not_integrated"),
        ("compare", "safe_comparable_manifest_not_integrated"),
    )
    run_kinds = mapping(resource.get("summary")).get("scheduled_run_kinds")
    execution_available = isinstance(run_kinds, list) and any(
        isinstance(kind, str) and bool(kind) for kind in run_kinds
    )
    return (
        WorkloadFeatureAvailability(
            name="overview", availability=covered, reason_codes=covered_reasons
        ),
        WorkloadFeatureAvailability(
            name="pods",
            availability="partial",
            reason_codes=("direct_pod_relationship_is_bounded",),
        ),
        WorkloadFeatureAvailability(
            name="events",
            availability="partial",
            reason_codes=("direct_event_relationship_is_bounded",),
        ),
        WorkloadFeatureAvailability(
            name="logs",
            availability=log_stream.availability,
            reason_codes=log_stream.reason_codes,
        ),
        WorkloadFeatureAvailability(
            name="execution",
            availability=covered if execution_available else "unavailable",
            reason_codes=(
                covered_reasons
                if execution_available
                else ("scheduled_run_projection_not_available",)
            ),
        ),
        WorkloadFeatureAvailability(
            name="rightsizing",
            availability=rightsizing.availability,
            reason_codes=rightsizing.reason_codes,
        ),
        *(
            WorkloadFeatureAvailability(
                name=name, availability="unavailable", reason_codes=(reason,)
            )
            for name, reason in unavailable
        ),
    )


def cluster_liveness(db: Any, workspace_id: str, cluster_id: str) -> tuple[str | None, str | None]:
    reader = getattr(db, "latest_cluster_agent_statuses", None)
    if not callable(reader):
        return None, "agent_liveness_unavailable"
    latest = reader(workspace_id, {cluster_id})
    agent = latest.get(cluster_id) if isinstance(latest, Mapping) else None
    return cluster_connection_status(agent), None


def mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def nested_mapping(value: object, key: str) -> dict[str, Any]:
    return mapping(mapping(value).get(key))


def required_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def text_or_none(value: object) -> str | None:
    text = required_text(value)
    return text or None


def safe_health(value: object) -> str:
    return text_or_none(value) or "unknown"


def nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
