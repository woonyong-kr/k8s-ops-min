"""Canonical product resource taxonomy for the inventory read model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from domains.inventory.snapshot_evidence import snapshot_source_summary
from packages.contracts.kubernetes_discovery import ApiResourceDiscoveryObservation

WORKLOAD_RESOURCE_TYPE = "workload"

WORKLOAD_KIND_BY_RESOURCE_TYPE = {
    "deployment": "Deployment",
    "statefulset": "StatefulSet",
    "daemonset": "DaemonSet",
    "replicaset": "ReplicaSet",
    "job": "Job",
    "cronjob": "CronJob",
}

RESOURCE_TYPE_BY_WORKLOAD_KIND = {
    kind.casefold(): resource_type for resource_type, kind in WORKLOAD_KIND_BY_RESOURCE_TYPE.items()
}

# This is the only mapping from Kubernetes discovery identities to product
# resource types. The browser consumes server-projected counts and never has to
# maintain a second copy of this taxonomy.
RESOURCE_TYPE_BY_DISCOVERY_IDENTITY = {
    ("v1", "Pod"): "pod",
    ("v1", "Node"): "node",
    ("v1", "ConfigMap"): "configmap",
    ("v1", "Secret"): "secret",
    ("apps/v1", "Deployment"): "deployment",
    ("apps/v1", "StatefulSet"): "statefulset",
    ("apps/v1", "DaemonSet"): "daemonset",
    ("apps/v1", "ReplicaSet"): "replicaset",
    ("batch/v1", "Job"): "job",
    ("batch/v1", "CronJob"): "cronjob",
    ("v1", "Service"): "service",
    ("discovery.k8s.io/v1", "EndpointSlice"): "endpoint",
    ("networking.k8s.io/v1", "Ingress"): "ingress",
    ("networking.k8s.io/v1", "NetworkPolicy"): "networkpolicy",
    ("autoscaling/v2", "HorizontalPodAutoscaler"): "hpa",
    ("autoscaling/v1", "HorizontalPodAutoscaler"): "hpa",
    ("v1", "PersistentVolumeClaim"): "pvc",
    ("v1", "PersistentVolume"): "persistentvolume",
    ("storage.k8s.io/v1", "StorageClass"): "storageclass",
    ("v1", "ResourceQuota"): "resourcequota",
    ("v1", "Event"): "event",
}


def workload_kind_for_resource_type(resource_type: str | None) -> str | None:
    if resource_type is None:
        return None
    return WORKLOAD_KIND_BY_RESOURCE_TYPE.get(resource_type.strip().casefold())


def resource_type_for_workload_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    return RESOURCE_TYPE_BY_WORKLOAD_KIND.get(kind.strip().casefold())


def project_inventory_product_counts(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Project stored family/kind rows into one non-duplicated product catalog."""

    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        stored_type = str(row.get("resource_type") or "").strip().casefold()
        health = str(row.get("health") or "unknown").strip().casefold()
        if not stored_type or not health:
            continue
        resource_type = stored_type
        if stored_type == WORKLOAD_RESOURCE_TYPE:
            resource_type = (
                resource_type_for_workload_kind(str(row.get("kind") or "")) or stored_type
            )
        count = int(row.get("count") or 0)
        key = (resource_type, health)
        counts[key] = counts.get(key, 0) + count
    return [
        {"resource_type": resource_type, "health": health, "count": count}
        for (resource_type, health), count in sorted(counts.items())
    ]


def discoverable_product_resource_types(
    snapshot: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Return listable product types from one exact, persisted discovery cut."""

    source = snapshot_source_summary(snapshot)
    payload = source.get("api_resource_discovery") if source is not None else None
    if not isinstance(payload, Mapping):
        return ()
    try:
        discovery = ApiResourceDiscoveryObservation.model_validate(payload)
    except ValidationError:
        return ()
    if discovery.completeness != "exact" or discovery.reason_codes:
        return ()
    return tuple(
        sorted(
            {
                resource_type
                for resource in discovery.resources
                if "list" in resource.verbs
                and (
                    resource_type := RESOURCE_TYPE_BY_DISCOVERY_IDENTITY.get(
                        (resource.api_version, resource.kind)
                    )
                )
                is not None
            }
        )
    )


def include_discoverable_zero_counts(
    rows: Sequence[Mapping[str, object]],
    *,
    snapshot: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    """Expose supported-but-empty types without browser-side inference."""

    projected = [dict(row) for row in rows]
    present = {str(row.get("resource_type") or "").casefold() for row in projected}
    projected.extend(
        {"resource_type": resource_type, "health": "unknown", "count": 0}
        for resource_type in discoverable_product_resource_types(snapshot)
        if resource_type not in present
    )
    return sorted(
        projected,
        key=lambda row: (str(row["resource_type"]), str(row["health"])),
    )
