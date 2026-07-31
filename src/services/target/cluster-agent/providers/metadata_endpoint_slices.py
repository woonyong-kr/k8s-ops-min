from __future__ import annotations

from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject
from providers.kubernetes_utils import (
    K8S_ENDPOINT_SLICE_SERVICE_NAME_LABEL,
    compact_dict,
    list_items,
    metadata,
    object_or_empty,
    resource_identity_key,
    resource_identity_snapshot,
    resource_sort_key,
)

ENDPOINT_CONDITION_READY = "ready"
ENDPOINT_CONDITION_SERVING = "serving"
ENDPOINT_CONDITION_TERMINATING = "terminating"
ENDPOINT_CONDITION_DEFAULTS = {
    ENDPOINT_CONDITION_READY: True,
    ENDPOINT_CONDITION_SERVING: True,
    ENDPOINT_CONDITION_TERMINATING: False,
}
MAX_ENDPOINT_PORTS = 10
MAX_READY_TARGET_REFS = 25


def endpoint_slice_ready_endpoint_snapshots(
    endpoint_slices: list[JsonObject],
    *,
    service_matches: list[JsonObject] | None = None,
) -> list[JsonObject]:
    """Build EndpointSlice readiness summaries for RCA."""
    service_filter = service_keys_from_matches(service_matches)
    snapshots: list[JsonObject] = []
    for endpoint_slice in sorted(endpoint_slices, key=resource_sort_key):
        service = endpoint_slice_service_identity(endpoint_slice)
        if not endpoint_slice_in_service_filter(service, service_filter):
            continue
        snapshots.append(endpoint_slice_ready_endpoint_snapshot(endpoint_slice, service))
    return snapshots


def endpoint_slice_ready_endpoint_snapshot(
    endpoint_slice: JsonObject,
    service: JsonObject,
) -> JsonObject:
    """Build one EndpointSlice readiness summary."""
    endpoints = list_items(endpoint_slice.get("endpoints"))
    ready_endpoints = [
        endpoint
        for endpoint in endpoints
        if endpoint_condition(endpoint, ENDPOINT_CONDITION_READY) is True
    ]
    ready_states = [
        endpoint_condition(endpoint, ENDPOINT_CONDITION_READY) for endpoint in endpoints
    ]
    ready_targets = ready_target_snapshots(ready_endpoints, endpoint_slice)
    ports = list_items(endpoint_slice.get("ports"))
    snapshot = compact_dict(
        {
            "service": service,
            "endpoint_slice": resource_identity_snapshot(endpoint_slice),
            "address_type": endpoint_slice.get("addressType"),
            "ports": endpoint_ports_snapshot(ports),
            "endpoint_count": len(endpoints),
            "ready_endpoint_count": ready_states.count(True),
            "not_ready_endpoint_count": ready_states.count(False),
            "unknown_ready_endpoint_count": ready_states.count(None),
            "serving_endpoint_count": endpoint_condition_count(
                endpoints,
                ENDPOINT_CONDITION_SERVING,
            ),
            "terminating_endpoint_count": endpoint_condition_count(
                endpoints,
                ENDPOINT_CONDITION_TERMINATING,
            ),
            "ready_targets": ready_targets[:MAX_READY_TARGET_REFS],
        }
    )
    if len(ports) > MAX_ENDPOINT_PORTS:
        snapshot["ports_truncated"] = True
    if len(ready_targets) > MAX_READY_TARGET_REFS:
        snapshot["ready_targets_truncated"] = True
    return snapshot


def endpoint_slice_service_identity(endpoint_slice: JsonObject) -> JsonObject:
    """Return the Service identity from an EndpointSlice label."""
    meta = metadata(endpoint_slice)
    labels = object_or_empty(meta.get("labels"))
    return compact_dict(
        {
            "namespace": meta.get("namespace"),
            "name": labels.get(K8S_ENDPOINT_SLICE_SERVICE_NAME_LABEL),
        }
    )


def endpoint_slice_in_service_filter(
    service: JsonObject,
    service_filter: set[tuple[str, str]] | None,
) -> bool:
    """Check whether this EndpointSlice belongs in the selected Service set."""
    if not service:
        return False
    if service_filter is None:
        return True
    return resource_identity_key(service) in service_filter


def endpoint_ports_snapshot(value: Any) -> list[JsonObject]:
    """Return small EndpointSlice port summaries."""
    ports: list[JsonObject] = []
    for port in list_items(value)[:MAX_ENDPOINT_PORTS]:
        snapshot = compact_dict(
            {
                "name": port.get("name"),
                "port": port.get("port"),
                "protocol": port.get("protocol"),
                "app_protocol": port.get("appProtocol"),
            }
        )
        if snapshot:
            ports.append(snapshot)
    return ports


def endpoint_condition_count(endpoints: list[JsonObject], condition_name: str) -> int:
    """Count endpoints where one condition is true."""
    return sum(1 for endpoint in endpoints if endpoint_condition(endpoint, condition_name) is True)


def endpoint_condition(endpoint: JsonObject, condition_name: str) -> bool | None:
    """Return one EndpointSlice condition or its Kubernetes default."""
    conditions = object_or_empty(endpoint.get("conditions"))
    value = conditions.get(condition_name)
    if condition_name not in conditions or value is None:
        return ENDPOINT_CONDITION_DEFAULTS.get(condition_name)
    return value if isinstance(value, bool) else None


def ready_target_snapshots(
    ready_endpoints: list[JsonObject],
    endpoint_slice: JsonObject,
) -> list[JsonObject]:
    """Return target refs for ready endpoints only."""
    snapshots: list[JsonObject] = []
    for endpoint in ready_endpoints:
        snapshot = target_ref_snapshot(endpoint, endpoint_slice)
        if snapshot:
            snapshots.append(snapshot)
    return snapshots


def target_ref_snapshot(
    endpoint: JsonObject,
    endpoint_slice: JsonObject,
) -> JsonObject:
    """Return the endpoint target object without addresses."""
    target_ref = object_or_empty(endpoint.get("targetRef"))
    return compact_dict(
        {
            "kind": target_ref.get("kind"),
            "namespace": target_ref.get("namespace") or metadata(endpoint_slice).get("namespace"),
            "name": target_ref.get("name"),
        }
    )


def service_keys_from_matches(
    service_matches: list[JsonObject] | None,
) -> set[tuple[str, str]] | None:
    """Return service identity keys from Service selector matches."""
    if service_matches is None:
        return None
    keys: set[tuple[str, str]] = set()
    for match in service_matches:
        key = resource_identity_key(object_or_empty(match.get("service")))
        if key:
            keys.add(key)
    return keys
