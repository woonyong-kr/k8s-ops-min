"""Allowlisted inventory projections for the read-only Compare route."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from domains.inventory.snapshot_evidence import snapshot_source_summary
from domains.target.connectivity import (
    AGENT_STATUS_NEVER_CONNECTED,
    AGENT_STATUS_ONLINE,
    AGENT_STATUS_STALE,
    cluster_connection_status,
)
from packages.contracts.comparable_manifest import (
    ComparableManifest,
    CompareCandidate,
    CompareCandidateList,
    CompareCoverage,
    CompareDescriptor,
    CompareMetadata,
    ComparePresentation,
    CompareProvenance,
    CompareResourcePair,
    ServicePortProjection,
    ServicePortsProjection,
    WorkloadReplicaProjection,
)
from packages.contracts.parity import ClusterScope, ResourceRef

MAX_COMPARE_CANDIDATES = 200
MAX_SERVICE_PORTS = 64
PORT_PROTOCOLS = frozenset({"TCP", "UDP", "SCTP"})
SERVICE_TYPES = frozenset({"ClusterIP", "NodePort", "LoadBalancer", "ExternalName"})
PORT_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


@dataclass(frozen=True)
class CompareDescriptorDefinition:
    route_kind: str
    api_group: str
    api_version: str
    kubernetes_kind: str
    resource_type: str
    projection_kind: str

    def contract(self) -> CompareDescriptor:
        return CompareDescriptor(
            route_kind=self.route_kind,
            api_group=self.api_group,
            api_version=self.api_version,
            kubernetes_kind=self.kubernetes_kind,
            resource_type=self.resource_type,
            projection_kind=self.projection_kind,
        )


# This registry is the single server-owned translation from the upstream plural
# URL key to a Kubernetes identity and an explicitly reviewed safe projection.
# Browser code must consume the descriptor returned by this module, never infer
# a plural name or resource type from a Kubernetes kind.
COMPARE_DESCRIPTOR_REGISTRY = (
    CompareDescriptorDefinition(
        "deployments", "apps", "v1", "Deployment", "workload", "workload_replicas"
    ),
    CompareDescriptorDefinition(
        "statefulsets", "apps", "v1", "StatefulSet", "workload", "workload_replicas"
    ),
    CompareDescriptorDefinition(
        "replicasets", "apps", "v1", "ReplicaSet", "workload", "workload_replicas"
    ),
    CompareDescriptorDefinition("services", "", "v1", "Service", "service", "service_ports"),
)


class CompareNotFound(LookupError):
    """An exact side is absent from the authorized inventory observation."""


class CompareUnavailable(RuntimeError):
    """The server cannot establish a safe inventory observation boundary."""


class CompareIdentityUnavailable(RuntimeError):
    """A row has no UID, so it cannot produce an exact ResourceRef."""


class CompareUnsupported(ValueError):
    """The requested route identity has no safe projection descriptor."""


@dataclass(frozen=True)
class CompareTarget:
    namespace: str | None
    name: str


@dataclass(frozen=True)
class ObservationContext:
    scope: ClusterScope
    latest_snapshot_id: str
    base_reason_codes: tuple[str, ...]


def comparison_descriptors() -> tuple[CompareDescriptor, ...]:
    return tuple(definition.contract() for definition in COMPARE_DESCRIPTOR_REGISTRY)


def resolve_descriptor(
    *,
    route_kind: str,
    api_group: str,
    api_version: str | None,
) -> CompareDescriptorDefinition:
    normalized_kind = canonical_route_kind(route_kind)
    normalized_group = canonical_group(api_group)
    normalized_version = canonical_version(api_version) if api_version is not None else None
    matches = [
        definition
        for definition in COMPARE_DESCRIPTOR_REGISTRY
        if definition.route_kind == normalized_kind
        and definition.api_group == normalized_group
        and (normalized_version is None or definition.api_version == normalized_version)
    ]
    if len(matches) != 1:
        raise CompareUnsupported("safe comparison descriptor is unavailable")
    return matches[0]


def parse_compare_target(value: str) -> CompareTarget:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 317:
        raise ValueError("comparison target is invalid")
    if value.count("/") == 0:
        return CompareTarget(namespace=None, name=canonical_name(value))
    if value.count("/") != 1:
        raise ValueError("comparison target is invalid")
    namespace, name = value.split("/", 1)
    return CompareTarget(namespace=canonical_namespace(namespace), name=canonical_name(name))


def compare_resource_pair(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    route_kind: str,
    api_group: str,
    api_version: str | None,
    a: CompareTarget,
    b: CompareTarget,
) -> CompareResourcePair:
    descriptor = resolve_descriptor(
        route_kind=route_kind,
        api_group=api_group,
        api_version=api_version,
    )
    if a == b:
        raise ValueError("comparison targets must differ")
    resource_a = exact_resource(db, workspace_id, cluster_id, descriptor, a)
    resource_b = exact_resource(db, workspace_id, cluster_id, descriptor, b)
    context = observation_context(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespaces=(a.namespace, b.namespace),
        resources=(resource_a, resource_b),
    )
    manifest_a = comparable_manifest(resource_a, descriptor, context)
    manifest_b = comparable_manifest(resource_b, descriptor, context)
    coverage = pair_coverage(context, manifest_a.provenance, manifest_b.provenance)
    return CompareResourcePair(
        scope=context.scope,
        descriptor=descriptor.contract(),
        coverage=coverage,
        presentation=ComparePresentation(),
        a=manifest_a,
        b=manifest_b,
    )


def compare_candidates(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    route_kind: str,
    api_group: str,
    api_version: str | None,
) -> CompareCandidateList:
    descriptor = resolve_descriptor(
        route_kind=route_kind,
        api_group=api_group,
        api_version=api_version,
    )
    reader = getattr(db, "list_inventory_resources_by_api_version", None)
    if not callable(reader):
        raise CompareUnavailable("exact inventory candidate reader is unavailable")
    rows = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=descriptor.resource_type,
        api_version=full_api_version(descriptor),
        kind=descriptor.kubernetes_kind,
        limit=MAX_COMPARE_CANDIDATES,
    )
    if not isinstance(rows, Iterable):
        raise CompareUnavailable("exact inventory candidate reader is unavailable")
    resources = [dict(row) for row in rows if isinstance(row, Mapping)]
    context = observation_context(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespaces=tuple(text_or_none(resource.get("namespace")) for resource in resources),
        resources=resources,
    )
    candidates: list[CompareCandidate] = []
    excluded_count = 0
    for resource in resources:
        try:
            candidates.append(
                CompareCandidate(
                    resource=resource_ref_from(resource, descriptor),
                    provenance=provenance_for(resource, context),
                )
            )
        except CompareIdentityUnavailable:
            excluded_count += 1
    return CompareCandidateList(
        scope=context.scope,
        descriptor=descriptor.contract(),
        coverage=coverage_from_context(context, resources),
        candidates=tuple(candidates),
        excluded_count=excluded_count,
    )


def exact_resource(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    descriptor: CompareDescriptorDefinition,
    target: CompareTarget,
) -> dict[str, Any]:
    reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(reader):
        raise CompareUnavailable("exact inventory identity reader is unavailable")
    resource = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=descriptor.resource_type,
        api_version=full_api_version(descriptor),
        kind=descriptor.kubernetes_kind,
        namespace=target.namespace,
        name=target.name,
    )
    if not isinstance(resource, Mapping):
        raise CompareNotFound()
    item = dict(resource)
    if (
        not exact_descriptor_identity(item, descriptor)
        or text_or_none(item.get("name")) != target.name
    ):
        raise CompareNotFound()
    if text_or_none(item.get("namespace")) != target.namespace:
        raise CompareNotFound()
    return item


def comparable_manifest(
    resource: Mapping[str, Any],
    descriptor: CompareDescriptorDefinition,
    context: ObservationContext,
) -> ComparableManifest:
    resource_ref = resource_ref_from(resource, descriptor)
    projection, omitted_paths = projection_from(resource, descriptor)
    return ComparableManifest(
        resource=resource_ref,
        metadata=CompareMetadata(name=resource_ref.name, namespace=resource_ref.namespace),
        projection=projection,
        provenance=provenance_for(resource, context),
        omitted_paths=omitted_paths,
    )


def projection_from(
    resource: Mapping[str, Any],
    descriptor: CompareDescriptorDefinition,
) -> tuple[WorkloadReplicaProjection | ServicePortsProjection, tuple[str, ...]]:
    summary = mapping(resource.get("summary"))
    if descriptor.projection_kind == "workload_replicas":
        replicas = explicit_nonnegative_int(summary.get("desired_replicas"))
        return WorkloadReplicaProjection(replicas=replicas), (
            () if replicas is not None else ("spec.replicas",)
        )
    if descriptor.projection_kind == "service_ports":
        service_type = safe_service_type(summary.get("type"))
        ports, excluded_count = safe_service_ports(summary.get("ports"))
        omitted_paths: list[str] = []
        if service_type is None:
            omitted_paths.append("spec.type")
        if excluded_count:
            omitted_paths.append("spec.ports")
        return (
            ServicePortsProjection(
                service_type=service_type,
                ports=tuple(ports),
                excluded_port_count=excluded_count,
            ),
            tuple(omitted_paths),
        )
    raise CompareUnsupported("safe comparison projection is unavailable")


def safe_service_ports(value: object) -> tuple[list[ServicePortProjection], int]:
    if not isinstance(value, list):
        return [], 0
    projected: list[ServicePortProjection] = []
    excluded = 0
    for item in value:
        if len(projected) >= MAX_SERVICE_PORTS:
            excluded += 1
            continue
        if not isinstance(item, Mapping):
            excluded += 1
            continue
        port = bounded_port_number(item.get("port"))
        if port is None:
            excluded += 1
            continue
        target_name, target_number = safe_target_port(item.get("targetPort"))
        name = safe_port_name(item.get("name"))
        protocol = item.get("protocol")
        projected.append(
            ServicePortProjection(
                name=name,
                port=port,
                protocol=protocol if protocol in PORT_PROTOCOLS else None,
                target_port_name=target_name,
                target_port_number=target_number,
                node_port=bounded_port_number(item.get("nodePort")),
            )
        )
    return projected, excluded


def safe_target_port(value: object) -> tuple[str | None, int | None]:
    numeric = bounded_port_number(value)
    if numeric is not None:
        return None, numeric
    named = safe_port_name(value)
    return named, None


def observation_context(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespaces: Iterable[str | None],
    resources: Iterable[Mapping[str, Any]],
) -> ObservationContext:
    reader = getattr(db, "latest_inventory_snapshot", None)
    if not callable(reader):
        raise CompareUnavailable("inventory snapshot metadata is unavailable")
    latest = reader(workspace_id, cluster_id)
    if not isinstance(latest, Mapping):
        raise CompareUnavailable("inventory snapshot metadata is unavailable")
    latest_snapshot_id = text_or_none(latest.get("snapshot_id"))
    if latest_snapshot_id is None:
        raise CompareUnavailable("inventory snapshot identity is unavailable")
    summary = snapshot_source_summary(latest) or {}
    reasons: list[str] = []
    limits = mapping(summary.get("collection_limits"))
    if limits.get("truncated") is True:
        reasons.append("source_resources_truncated")
    elif summary.get("resources_complete") is not True:
        reasons.append("source_resources_incomplete")
    connection, liveness_reason = cluster_liveness(db, workspace_id, cluster_id)
    freshness = freshness_for(connection)
    if liveness_reason is not None:
        reasons.append(liveness_reason)
    elif connection != AGENT_STATUS_ONLINE:
        reasons.append("cluster_not_live")
    namespace_set = tuple(sorted({namespace for namespace in namespaces if namespace}))
    scope = ClusterScope(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespaces=namespace_set,
        freshness=freshness,
    )
    return ObservationContext(scope, latest_snapshot_id, tuple(unique(reasons)))


def provenance_for(resource: Mapping[str, Any], context: ObservationContext) -> CompareProvenance:
    snapshot_id = text_or_none(resource.get("snapshot_id"))
    if snapshot_id is None:
        raise CompareUnavailable("resource snapshot identity is unavailable")
    reasons = list(context.base_reason_codes)
    if snapshot_id != context.latest_snapshot_id:
        reasons.append("resource_not_observed_in_latest_snapshot")
    return CompareProvenance(
        observation_snapshot_id=snapshot_id,
        latest_snapshot_id=context.latest_snapshot_id,
        observed_at=text_or_none(resource.get("observed_at")),
        availability="available" if not reasons else "partial",
        reason_codes=tuple(unique(reasons)),
    )


def pair_coverage(
    context: ObservationContext,
    a: CompareProvenance,
    b: CompareProvenance,
) -> CompareCoverage:
    reasons = [*context.base_reason_codes, *a.reason_codes, *b.reason_codes]
    if a.observation_snapshot_id != b.observation_snapshot_id:
        reasons.append("sides_observed_in_different_snapshots")
    reasons = unique(reasons)
    return CompareCoverage(
        availability="available" if not reasons else "partial",
        latest_snapshot_id=context.latest_snapshot_id,
        reason_codes=tuple(reasons),
    )


def coverage_from_context(
    context: ObservationContext,
    resources: Iterable[Mapping[str, Any]],
) -> CompareCoverage:
    reasons = list(context.base_reason_codes)
    snapshot_ids = {
        snapshot_id
        for resource in resources
        if (snapshot_id := text_or_none(resource.get("snapshot_id"))) is not None
    }
    if any(snapshot_id != context.latest_snapshot_id for snapshot_id in snapshot_ids):
        reasons.append("candidate_set_not_observed_in_latest_snapshot")
    if len(snapshot_ids) > 1:
        reasons.append("candidate_set_spans_observation_snapshots")
    reasons = unique(reasons)
    return CompareCoverage(
        availability="available" if not reasons else "partial",
        latest_snapshot_id=context.latest_snapshot_id,
        reason_codes=tuple(reasons),
    )


def resource_ref_from(
    resource: Mapping[str, Any], descriptor: CompareDescriptorDefinition
) -> ResourceRef:
    if not exact_descriptor_identity(resource, descriptor):
        raise CompareNotFound()
    uid = text_or_none(resource.get("uid"))
    name = text_or_none(resource.get("name"))
    if uid is None:
        raise CompareIdentityUnavailable("comparison resource UID is unavailable")
    if name is None:
        raise CompareUnavailable("comparison resource identity is unavailable")
    return ResourceRef(
        api_group=descriptor.api_group,
        version=descriptor.api_version,
        kind=descriptor.kubernetes_kind,
        namespace=text_or_none(resource.get("namespace")),
        name=name,
        uid=uid,
    )


def exact_descriptor_identity(
    resource: Mapping[str, Any], descriptor: CompareDescriptorDefinition
) -> bool:
    return (
        text_or_none(resource.get("resource_type")) == descriptor.resource_type
        and text_or_none(resource.get("api_version")) == full_api_version(descriptor)
        and text_or_none(resource.get("kind")) == descriptor.kubernetes_kind
    )


def full_api_version(descriptor: CompareDescriptorDefinition) -> str:
    return (
        f"{descriptor.api_group}/{descriptor.api_version}"
        if descriptor.api_group
        else descriptor.api_version
    )


def cluster_liveness(db: Any, workspace_id: str, cluster_id: str) -> tuple[str | None, str | None]:
    reader = getattr(db, "latest_cluster_agent_statuses", None)
    if not callable(reader):
        return None, "agent_liveness_unavailable"
    latest = reader(workspace_id, {cluster_id})
    agent = latest.get(cluster_id) if isinstance(latest, Mapping) else None
    return cluster_connection_status(agent), None


def freshness_for(connection: str | None) -> str:
    if connection == AGENT_STATUS_ONLINE:
        return "live"
    if connection == AGENT_STATUS_STALE:
        return "stale"
    if connection == AGENT_STATUS_NEVER_CONNECTED:
        return "disconnected"
    return "partial"


def canonical_route_kind(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or "/" in value
        or any(char.isspace() for char in value)
    ):
        raise ValueError("comparison route kind is invalid")
    return value.casefold()


def canonical_group(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or "/" in value
        or any(char.isspace() for char in value)
    ):
        raise ValueError("comparison API group is invalid")
    return value


def canonical_version(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or "/" in value
        or any(char.isspace() for char in value)
    ):
        raise ValueError("comparison API version is invalid")
    return value


def canonical_namespace(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not PORT_NAME_PATTERN.fullmatch(value)
    ):
        raise ValueError("comparison namespace is invalid")
    return value


def canonical_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 253
        or "/" in value
        or any(char.isspace() for char in value)
    ):
        raise ValueError("comparison resource name is invalid")
    return value


def safe_service_type(value: object) -> str | None:
    return value if isinstance(value, str) and value in SERVICE_TYPES else None


def bounded_port_number(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535
        else None
    )


def safe_port_name(value: object) -> str | None:
    return value if isinstance(value, str) and PORT_NAME_PATTERN.fullmatch(value) else None


def explicit_nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def text_or_none(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
