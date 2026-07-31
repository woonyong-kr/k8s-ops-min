from __future__ import annotations

from packages.contracts.event_bus.interfaces import JsonObject
from providers.kubernetes_utils import (
    compact_dict,
    metadata,
    object_or_empty,
    resource_identity_snapshot,
    resource_sort_key,
    spec,
)

SERVICE_SELECTOR_STATUS_MATCHED = "matched"
SERVICE_SELECTOR_STATUS_NO_MATCHING_PODS = "no_matching_pods"
SERVICE_SELECTOR_STATUS_SELECTOR_MISSING = "selector_missing"
SERVICE_TARGET_RELATION_EXACT_SELECTOR_MATCH = "exact_selector_match"
SERVICE_TARGET_RELATION_LIVE_POD_MATCH = "live_pod_match"
SERVICE_TARGET_RELATION_SELECTOR_KEY_OVERLAP = "selector_key_overlap"
MAX_MATCHED_POD_REFS = 25


def service_selector_match_snapshots(
    services: list[JsonObject],
    pods: list[JsonObject],
    *,
    target_labels: JsonObject | None = None,
    target_pods: list[JsonObject] | None = None,
) -> list[JsonObject]:
    """Build Service selector to Pod label match summaries."""
    snapshots: list[JsonObject] = []
    for service in sorted(services, key=resource_sort_key):
        snapshot = service_selector_match_snapshot(
            service,
            pods,
            target_labels=target_labels,
            target_pods=target_pods,
        )
        if target_labels is None and target_pods is None:
            snapshots.append(snapshot)
        elif snapshot.get("target_relation"):
            snapshots.append(snapshot)
    return snapshots


def service_selector_match_snapshot(
    service: JsonObject,
    pods: list[JsonObject],
    *,
    target_labels: JsonObject | None = None,
    target_pods: list[JsonObject] | None = None,
) -> JsonObject:
    """Build one Service selector match summary."""
    selector = object_or_empty(spec(service).get("selector"))
    matched_pods = [
        pod
        for pod in sorted(pods, key=resource_sort_key)
        if selector
        and selector_matches_labels(
            selector,
            object_or_empty(metadata(pod).get("labels")),
        )
    ]
    matched_pod_refs = [resource_identity_snapshot(pod) for pod in matched_pods]
    snapshot = compact_dict(
        {
            "service": resource_identity_snapshot(service),
            "selector": selector,
            "match_status": service_selector_match_status(selector, matched_pods),
            "target_relation": service_target_relation(
                selector,
                target_labels,
                target_pods,
            ),
            "matched_pod_count": len(matched_pods),
            "matched_pods": matched_pod_refs[:MAX_MATCHED_POD_REFS],
        }
    )
    if len(matched_pod_refs) > MAX_MATCHED_POD_REFS:
        snapshot["matched_pods_truncated"] = True
    return snapshot


def service_selector_match_status(
    selector: JsonObject,
    matched_pods: list[JsonObject],
) -> str:
    """Return a small status for one Service selector match."""
    if not selector:
        return SERVICE_SELECTOR_STATUS_SELECTOR_MISSING
    if matched_pods:
        return SERVICE_SELECTOR_STATUS_MATCHED
    return SERVICE_SELECTOR_STATUS_NO_MATCHING_PODS


def selector_matches_labels(selector: JsonObject, labels: JsonObject) -> bool:
    """Check whether all selector labels exist on a Pod."""
    return all(labels.get(key) == value for key, value in selector.items())


def service_target_relation(
    selector: JsonObject,
    target_labels: JsonObject | None,
    target_pods: list[JsonObject] | None,
) -> str | None:
    """Return why a Service is related to the target Deployment."""
    if not selector:
        return None

    if target_labels and selector_matches_labels(selector, target_labels):
        return SERVICE_TARGET_RELATION_EXACT_SELECTOR_MATCH

    pod_labels = [object_or_empty(metadata(pod).get("labels")) for pod in target_pods or []]
    if any(selector_matches_labels(selector, labels) for labels in pod_labels):
        return SERVICE_TARGET_RELATION_LIVE_POD_MATCH

    target_label_keys = set(target_labels or {})
    for labels in pod_labels:
        target_label_keys.update(labels)
    if target_label_keys.intersection(selector):
        return SERVICE_TARGET_RELATION_SELECTOR_KEY_OVERLAP

    return None
