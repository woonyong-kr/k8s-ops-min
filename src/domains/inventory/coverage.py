"""Inventory collection coverage helpers.

The cluster-agent can collect a bounded namespace cut without proving full-cluster
liveness.  Keep the proof small and explicit so writers may delete only inside
the scopes the snapshot actually observed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, false, or_

from packages.contracts.event_bus.interfaces import JsonObject

COLLECTION_COVERAGE_SUMMARY_KEY = "collection_coverage"
COLLECTION_STATUS_KEY = "collection_status"

POD_COLLECTION = "pods"
NODE_COLLECTION = "nodes"
WORKLOAD_COLLECTION = "workloads"
WORKLOAD_REVISION_COLLECTION = "workload_revisions"
SERVICE_COLLECTION = "services"
INGRESS_COLLECTION = "ingresses"
RESOURCE_QUOTA_COLLECTION = "resourcequotas"
ENDPOINT_COLLECTION = "endpoints"
EVENT_COLLECTION = "events"
CUSTOM_RESOURCE_COLLECTION = "custom_resources"

RESOURCE_TYPES_BY_COLLECTION: dict[str, tuple[str, ...]] = {
    POD_COLLECTION: ("pod",),
    NODE_COLLECTION: ("node",),
    WORKLOAD_COLLECTION: ("workload",),
    WORKLOAD_REVISION_COLLECTION: ("workload_revision",),
    SERVICE_COLLECTION: ("service",),
    INGRESS_COLLECTION: ("ingress",),
    RESOURCE_QUOTA_COLLECTION: ("resourcequota",),
    ENDPOINT_COLLECTION: ("endpoint",),
    EVENT_COLLECTION: ("event",),
    CUSTOM_RESOURCE_COLLECTION: ("custom_resource",),
}

NAMESPACED_COLLECTIONS = (
    POD_COLLECTION,
    WORKLOAD_COLLECTION,
    WORKLOAD_REVISION_COLLECTION,
    SERVICE_COLLECTION,
    INGRESS_COLLECTION,
    RESOURCE_QUOTA_COLLECTION,
    ENDPOINT_COLLECTION,
    EVENT_COLLECTION,
)
CLUSTER_COLLECTIONS = (NODE_COLLECTION,)

DELETE_SAFE_COLLECTIONS = frozenset(
    (
        POD_COLLECTION,
        NODE_COLLECTION,
        WORKLOAD_COLLECTION,
        WORKLOAD_REVISION_COLLECTION,
        SERVICE_COLLECTION,
        INGRESS_COLLECTION,
        RESOURCE_QUOTA_COLLECTION,
        ENDPOINT_COLLECTION,
    )
)

SCOPED_INVENTORY_COLLECTIONS = (
    POD_COLLECTION,
    WORKLOAD_COLLECTION,
    WORKLOAD_REVISION_COLLECTION,
    NODE_COLLECTION,
    SERVICE_COLLECTION,
    INGRESS_COLLECTION,
    EVENT_COLLECTION,
    ENDPOINT_COLLECTION,
    RESOURCE_QUOTA_COLLECTION,
    CUSTOM_RESOURCE_COLLECTION,
)

LEGACY_LIVE_INVENTORY_COLLECTIONS = (
    POD_COLLECTION,
    WORKLOAD_COLLECTION,
    WORKLOAD_REVISION_COLLECTION,
    NODE_COLLECTION,
    SERVICE_COLLECTION,
    INGRESS_COLLECTION,
    ENDPOINT_COLLECTION,
    RESOURCE_QUOTA_COLLECTION,
)


@dataclass(frozen=True)
class InventoryDeleteScope:
    resource_type: str
    namespace: str | None


def kubernetes_collection_coverage(kubernetes: Mapping[str, Any]) -> list[JsonObject]:
    """Project collection-level coverage from a normalized Kubernetes evidence payload."""

    scopes = _collection_scopes(kubernetes)
    if not scopes:
        return []

    limits = _collection_limit_map(kubernetes)
    statuses = _collection_status_map(kubernetes)
    coverage: list[JsonObject] = []
    for scope in scopes:
        namespace = _text(scope.get("namespace"))
        label_selector = _text(scope.get("label_selector"))
        for collection in NAMESPACED_COLLECTIONS:
            collection_status = _collection_status(statuses, collection)
            coverage.append(
                _coverage_entry(
                    collection,
                    scope="namespace",
                    namespace=namespace,
                    label_selector=label_selector,
                    observed=_collection_observed(kubernetes, collection, collection_status),
                    truncated=_collection_truncated(limits, collection),
                    reason_codes=_reason_codes(collection_status.get("reason_codes")),
                )
            )

    for collection in CLUSTER_COLLECTIONS:
        collection_status = _collection_status(statuses, collection)
        coverage.append(
            _coverage_entry(
                collection,
                scope="cluster",
                namespace=None,
                label_selector="",
                observed=_collection_observed(kubernetes, collection, collection_status),
                truncated=_collection_truncated(limits, collection),
                reason_codes=_reason_codes(collection_status.get("reason_codes")),
            )
        )
    return coverage


def inventory_deletion_scopes(source_summary: Mapping[str, Any]) -> tuple[InventoryDeleteScope, ...]:
    """Return resource scopes where absence in the latest snapshot proves deletion."""

    if source_summary.get("live_inventory") is not True:
        return ()
    raw_entries = source_summary.get(COLLECTION_COVERAGE_SUMMARY_KEY)
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, str | bytes):
        return ()

    scopes: set[InventoryDeleteScope] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            continue
        collection = _text(raw_entry.get("collection"))
        resource_types = RESOURCE_TYPES_BY_COLLECTION.get(collection)
        if collection not in DELETE_SAFE_COLLECTIONS or not resource_types:
            continue
        if not _delete_authoritative_entry(raw_entry):
            continue
        scope = _text(raw_entry.get("scope"))
        namespace = _text(raw_entry.get("namespace")) or None
        if collection in NAMESPACED_COLLECTIONS and scope == "namespace" and namespace:
            for resource_type in resource_types:
                scopes.add(InventoryDeleteScope(resource_type, namespace))
        elif collection in CLUSTER_COLLECTIONS and scope == "cluster":
            for resource_type in resource_types:
                scopes.add(InventoryDeleteScope(resource_type, None))
    return tuple(sorted(scopes, key=lambda item: (item.resource_type, item.namespace or "")))


def inventory_row_in_deletion_scopes(
    row: Mapping[str, Any],
    scopes: Sequence[InventoryDeleteScope],
) -> bool:
    resource_type = _text(row.get("resource_type"))
    namespace = row.get("namespace")
    normalized_namespace = str(namespace) if namespace is not None else None
    return any(
        scope.resource_type == resource_type and scope.namespace == normalized_namespace
        for scope in scopes
    )


def inventory_delete_scope_predicate(table: Any, scopes: Sequence[InventoryDeleteScope]) -> Any:
    clauses = []
    for scope in scopes:
        clause = table.c.resource_type == scope.resource_type
        if scope.namespace is None:
            clause = and_(clause, table.c.namespace.is_(None))
        else:
            clause = and_(clause, table.c.namespace == scope.namespace)
        clauses.append(clause)
    return or_(*clauses) if clauses else false()


def _coverage_entry(
    collection: str,
    *,
    scope: str,
    namespace: str | None,
    label_selector: str,
    observed: bool,
    truncated: bool,
    reason_codes: Sequence[str] = (),
) -> JsonObject:
    coverage_reasons: list[str] = list(reason_codes)
    if not observed:
        coverage_reasons.append("collection_not_observed")
    if scope == "namespace" and not namespace:
        coverage_reasons.append("namespace_scope_unavailable")
    if label_selector:
        coverage_reasons.append("label_selector_scope")
    if truncated:
        coverage_reasons.append("collection_truncated")
    delete_reasons = list(coverage_reasons)
    if collection not in DELETE_SAFE_COLLECTIONS:
        delete_reasons.append("collection_not_delete_authoritative")
    complete = not coverage_reasons
    delete_safe = not delete_reasons
    return {
        "collection": collection,
        "resource_types": list(RESOURCE_TYPES_BY_COLLECTION.get(collection, ())),
        "scope": scope,
        "namespace": namespace,
        "label_selector": label_selector or None,
        "observed": observed,
        "complete": complete,
        "delete_safe": delete_safe,
        "truncated": truncated,
        "reason_codes": sorted(set(delete_reasons)),
    }


def _collection_scopes(payload: Mapping[str, Any]) -> tuple[JsonObject, ...]:
    raw_scopes = payload.get("collection_scopes")
    if not isinstance(raw_scopes, Sequence) or isinstance(raw_scopes, str | bytes):
        return ()
    return tuple(dict(scope) for scope in raw_scopes if isinstance(scope, Mapping))


def _collection_limit_map(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    limits = payload.get("collection_limits")
    if not isinstance(limits, Mapping):
        return {}
    lists = limits.get("lists")
    return lists if isinstance(lists, Mapping) else {}


def _collection_status_map(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    statuses = payload.get(COLLECTION_STATUS_KEY)
    return statuses if isinstance(statuses, Mapping) else {}


def _collection_status(statuses: Mapping[str, Any], collection: str) -> Mapping[str, Any]:
    status = statuses.get(collection)
    return status if isinstance(status, Mapping) else {}


def _collection_truncated(limits: Mapping[str, Any], collection: str) -> bool:
    limit = limits.get(collection)
    return isinstance(limit, Mapping) and limit.get("truncated") is True


def _collection_observed(
    payload: Mapping[str, Any],
    collection: str,
    status: Mapping[str, Any],
) -> bool:
    if status.get("observed") is False:
        return False
    return isinstance(payload.get(collection), list)


def _reason_codes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(reason.strip() for reason in value if isinstance(reason, str) and reason.strip())


def _delete_authoritative_entry(entry: Mapping[str, Any]) -> bool:
    return (
        entry.get("complete") is True
        and entry.get("delete_safe") is True
        and entry.get("observed") is True
        and entry.get("truncated") is not True
        and not _text(entry.get("label_selector"))
        and not _reason_codes(entry.get("reason_codes"))
    )


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
