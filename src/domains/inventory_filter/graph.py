"""Deterministic, evidence-backed projection for the Resources graph surface."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

JsonObject = dict[str, Any]
SelectorResult = Literal["match", "no_match", "unknown"]

GRAPH_RELATION_VERSION = "v1"
DEFAULT_NODE_LIMIT = 200
DEFAULT_EDGE_LIMIT = 1000
KNOWN_CATEGORIES = {"workload", "pod", "node", "service", "endpoint", "event"}


def build_resource_graph(
    items: Sequence[Mapping[str, Any]],
    *,
    snapshot_revision: int,
    filter_fingerprint: str,
    source_complete: bool,
    labels_complete: bool,
    truncated: bool,
    node_limit: int = DEFAULT_NODE_LIMIT,
    edge_limit: int = DEFAULT_EDGE_LIMIT,
    omitted_node_count: int = 0,
    partial_reason_codes: Sequence[str] = (),
    cluster: Mapping[str, Any] | None = None,
    authorization_revision: str = "",
) -> JsonObject:
    """Build a bounded graph without guessing relationships from names or raw payloads."""
    if node_limit < 1 or edge_limit < 1:
        raise ValueError("graph limits must be positive")

    all_rows = sorted(items, key=_inventory_key)
    builder_omitted_node_count = max(0, len(all_rows) - node_limit)
    rows = all_rows[:node_limit]
    omitted_node_count = max(0, omitted_node_count) + builder_omitted_node_count
    truncated = bool(truncated or builder_omitted_node_count)
    nodes = [_node(row) for row in rows]
    node_ids = {str(node["node_id"]) for node in nodes}
    if len(node_ids) != len(nodes):
        raise ValueError("graph node identities must be unique")

    resources = {
        str(node["node_id"]): _mapping(row.get("resource"))
        for row, node in zip(rows, nodes, strict=True)
    }
    _require_single_cluster(resources.values())
    by_identity = _identity_index(resources)
    node_by_name = _node_name_index(resources)
    pods_by_namespace = _pods_by_namespace(resources)

    edges: list[JsonObject] = []
    selector_unknown = False
    owner_reference_incomplete = False
    for child_id, resource in resources.items():
        summary = _mapping(resource.get("summary"))
        namespace = _optional_text(resource.get("namespace"))
        owner_kind = _optional_text(summary.get("owner_kind"))
        owner_name = _optional_text(summary.get("owner_name"))
        owner_uid = _optional_text(summary.get("owner_uid"))
        if owner_kind and owner_name:
            if summary.get("owner_references_complete") is not True:
                owner_reference_incomplete = True
            candidates = by_identity.get((namespace, owner_kind.casefold(), owner_name), ())
            if owner_uid:
                candidates = tuple(
                    candidate
                    for candidate in candidates
                    if _optional_text(resources[candidate].get("uid")) == owner_uid
                )
            else:
                candidates = ()
                owner_reference_incomplete = True
            owner_id = _unique(candidates)
            if owner_id:
                edges.append(
                    _edge(
                        owner_id,
                        child_id,
                        kind="owns",
                        plane="ownership",
                        evidence_type="owner_reference",
                        authority="authoritative",
                        observed_at=_edge_observed_at(resources[owner_id], resource),
                        historical=_historical(resources[owner_id], resource),
                    )
                )

        if str(resource.get("resource_type") or "").casefold() == "pod":
            node_name = _optional_text(summary.get("node_name"))
            node_id = _unique(node_by_name.get(node_name or "", ())) if node_name else None
            if node_id:
                edges.append(
                    _edge(
                        child_id,
                        node_id,
                        kind="runs_on",
                        plane="placement",
                        evidence_type="node_assignment",
                        authority="authoritative",
                        observed_at=_edge_observed_at(resource, resources[node_id]),
                        historical=_historical(resource, resources[node_id]),
                    )
                )

    for source_id, resource in resources.items():
        resource_type = str(resource.get("resource_type") or "").casefold()
        if resource_type not in {"service", "workload"}:
            continue
        summary = _mapping(resource.get("summary"))
        selector = summary.get("selector")
        if not isinstance(selector, Mapping) or not selector:
            continue
        namespace = _optional_text(resource.get("namespace"))
        for pod_id in pods_by_namespace.get(namespace, ()):
            result = _selector_result(
                selector,
                _string_mapping(resources[pod_id].get("labels")),
                labels_complete=labels_complete,
                structured=resource_type == "workload",
            )
            if result == "unknown":
                selector_unknown = True
            if result != "match":
                continue
            edges.append(
                _edge(
                    source_id,
                    pod_id,
                    kind="selects",
                    plane=("network_configured" if resource_type == "service" else "ownership"),
                    evidence_type="selector_match",
                    authority="derived",
                    observed_at=_edge_observed_at(resource, resources[pod_id]),
                    historical=_historical(resource, resources[pod_id]),
                )
            )

    service_index = {key: ids for key, ids in by_identity.items() if key[1] == "service"}
    for endpoint_id, resource in resources.items():
        if str(resource.get("resource_type") or "").casefold() != "endpoint":
            continue
        service_name = _optional_text(_mapping(resource.get("summary")).get("service_name"))
        namespace = _optional_text(resource.get("namespace"))
        if not service_name:
            continue
        service_id = _unique(service_index.get((namespace, "service", service_name), ()))
        if service_id:
            edges.append(
                _edge(
                    service_id,
                    endpoint_id,
                    kind="routes_to",
                    plane="network_effective",
                    evidence_type="service_name_label",
                    authority="authoritative",
                    observed_at=_edge_observed_at(resources[service_id], resource),
                    historical=_historical(resources[service_id], resource),
                )
            )

    canonical_edges = sorted(
        {str(edge["edge_id"]): edge for edge in edges}.values(),
        key=lambda edge: (
            str(edge["kind"]),
            str(edge["from_node_id"]),
            str(edge["to_node_id"]),
            str(edge["edge_id"]),
        ),
    )
    omitted_edge_count = max(0, len(canonical_edges) - edge_limit)
    canonical_edges = canonical_edges[:edge_limit]

    reasons = {str(reason) for reason in partial_reason_codes if str(reason)}
    if not source_complete:
        reasons.add("source_resources_incomplete")
    if not labels_complete:
        reasons.add("source_labels_incomplete")
    if selector_unknown:
        reasons.add("selector_evidence_incomplete")
    if owner_reference_incomplete:
        reasons.add("owner_reference_source_incomplete")
    if truncated or omitted_node_count:
        reasons.add("graph_node_budget_exceeded")
    if omitted_edge_count:
        reasons.add("graph_edge_budget_exceeded")
    relation_completeness: Literal["exact", "partial", "unavailable"]
    if snapshot_revision <= 0:
        relation_completeness = "unavailable"
    else:
        relation_completeness = "partial" if reasons else "exact"

    incoming_owned = {str(edge["to_node_id"]) for edge in canonical_edges if edge["kind"] == "owns"}
    root_node_ids = sorted(node_ids - incoming_owned)
    effective_truncated = bool(truncated or omitted_node_count or omitted_edge_count)
    graph_revision = _graph_revision(
        snapshot_revision=snapshot_revision,
        filter_fingerprint=filter_fingerprint,
        node_ids=sorted(node_ids),
        nodes=nodes,
        edge_ids=[str(edge["edge_id"]) for edge in canonical_edges],
        edges=canonical_edges,
        node_limit=node_limit,
        edge_limit=edge_limit,
        authorization_revision=authorization_revision,
    )
    selected_cluster = dict(cluster or (rows[0].get("cluster") if rows else {}) or {})
    if "cluster_id" not in selected_cluster:
        selected_cluster["cluster_id"] = next(
            (str(resource.get("cluster_id")) for resource in resources.values()),
            "unknown",
        )
    return {
        "graph_revision": graph_revision,
        "cluster": selected_cluster,
        "nodes": nodes,
        "edges": canonical_edges,
        "root_node_ids": root_node_ids,
        "node_count": len(nodes),
        "edge_count": len(canonical_edges),
        "omitted_node_count": max(0, omitted_node_count),
        "omitted_edge_count": omitted_edge_count,
        "node_limit": node_limit,
        "edge_limit": edge_limit,
        "truncated": effective_truncated,
        "relation_completeness": relation_completeness,
        "partial_reason_codes": sorted(reasons),
    }


def _node(item: Mapping[str, Any]) -> JsonObject:
    resource = _mapping(item.get("resource"))
    node_id = _inventory_key(item)
    resource_type = str(resource.get("resource_type") or "").casefold()
    application_ids = sorted(
        {str(value) for value in item.get("application_ids", []) if str(value)}
    )
    return {
        "node_id": node_id,
        "category": resource_type if resource_type in KNOWN_CATEGORIES else "other",
        "identity": {
            "version": "v1",
            "cluster_id": str(resource.get("cluster_id") or ""),
            "resource_type": str(resource.get("resource_type") or ""),
            "api_version": str(resource.get("api_version") or ""),
            "kind": str(resource.get("kind") or ""),
            "namespace": _optional_text(resource.get("namespace")),
            "name": str(resource.get("name") or ""),
            "uid": _optional_text(resource.get("uid")),
        },
        "status": str(resource.get("status") or ""),
        "health": str(resource.get("health") or ""),
        "observed_at": _optional_text(resource.get("observed_at")),
        "deleted_at": _optional_text(resource.get("deleted_at")),
        "application_ids": application_ids,
        "application_binding_completeness": str(
            item.get("application_binding_completeness") or "unavailable"
        ),
    }


def _identity_index(
    resources: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str | None, str, str], tuple[str, ...]]:
    mutable: dict[tuple[str | None, str, str], list[str]] = {}
    for node_id, resource in resources.items():
        key = (
            _optional_text(resource.get("namespace")),
            str(resource.get("kind") or "").casefold(),
            str(resource.get("name") or ""),
        )
        mutable.setdefault(key, []).append(node_id)
    return {key: tuple(sorted(ids)) for key, ids in mutable.items()}


def _node_name_index(
    resources: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    mutable: dict[str, list[str]] = {}
    for node_id, resource in resources.items():
        if str(resource.get("resource_type") or "").casefold() != "node":
            continue
        mutable.setdefault(str(resource.get("name") or ""), []).append(node_id)
    return {key: tuple(sorted(ids)) for key, ids in mutable.items()}


def _pods_by_namespace(
    resources: Mapping[str, Mapping[str, Any]],
) -> dict[str | None, tuple[str, ...]]:
    mutable: dict[str | None, list[str]] = {}
    for node_id, resource in resources.items():
        if str(resource.get("resource_type") or "").casefold() != "pod":
            continue
        mutable.setdefault(_optional_text(resource.get("namespace")), []).append(node_id)
    return {key: tuple(sorted(ids)) for key, ids in mutable.items()}


def _selector_result(
    selector: Mapping[str, Any],
    labels: Mapping[str, str],
    *,
    labels_complete: bool,
    structured: bool,
) -> SelectorResult:
    if structured:
        if set(selector) - {"matchLabels", "matchExpressions"}:
            return "unknown"
        match_labels = selector.get("matchLabels", {})
        expressions = selector.get("matchExpressions", [])
        if not isinstance(match_labels, Mapping) or not isinstance(expressions, list):
            return "unknown"
        requirements = [
            _equals_requirement(str(key), str(value), labels, labels_complete=labels_complete)
            for key, value in match_labels.items()
        ]
        requirements.extend(
            _expression_result(expression, labels, labels_complete=labels_complete)
            for expression in expressions
        )
    else:
        requirements = [
            _equals_requirement(str(key), str(value), labels, labels_complete=labels_complete)
            for key, value in selector.items()
        ]
    if not requirements:
        return "unknown"
    if "no_match" in requirements:
        return "no_match"
    return "unknown" if "unknown" in requirements else "match"


def _equals_requirement(
    key: str,
    value: str,
    labels: Mapping[str, str],
    *,
    labels_complete: bool,
) -> SelectorResult:
    if key not in labels:
        return "no_match" if labels_complete else "unknown"
    return "match" if labels[key] == value else "no_match"


def _expression_result(
    expression: Any,
    labels: Mapping[str, str],
    *,
    labels_complete: bool,
) -> SelectorResult:
    if not isinstance(expression, Mapping):
        return "unknown"
    key = _optional_text(expression.get("key"))
    operator = _optional_text(expression.get("operator"))
    values = expression.get("values", [])
    if not key or not operator or not isinstance(values, list):
        return "unknown"
    normalized_values = {str(value) for value in values}
    present = key in labels
    if operator == "In":
        if not normalized_values:
            return "unknown"
        if not present:
            return "no_match" if labels_complete else "unknown"
        return "match" if labels[key] in normalized_values else "no_match"
    if operator == "NotIn":
        if not normalized_values:
            return "unknown"
        if not present:
            return "match" if labels_complete else "unknown"
        return "match" if labels[key] not in normalized_values else "no_match"
    if operator == "Exists":
        if normalized_values:
            return "unknown"
        if present:
            return "match"
        return "no_match" if labels_complete else "unknown"
    if operator == "DoesNotExist":
        if normalized_values:
            return "unknown"
        if present:
            return "no_match"
        return "match" if labels_complete else "unknown"
    return "unknown"


def _edge(
    from_node_id: str,
    to_node_id: str,
    *,
    kind: str,
    plane: str,
    evidence_type: str,
    authority: str,
    observed_at: str | None,
    historical: bool,
) -> JsonObject:
    identity = "\x1f".join(
        (GRAPH_RELATION_VERSION, kind, plane, from_node_id, to_node_id, evidence_type)
    )
    return {
        "edge_id": f"edge-{hashlib.sha256(identity.encode()).hexdigest()[:32]}",
        "from_node_id": from_node_id,
        "to_node_id": to_node_id,
        "kind": kind,
        "plane": plane,
        "direction": "directed",
        "state": "historical" if historical else "active",
        "evidence": {
            "type": evidence_type,
            "authority": authority,
            "observed_at": observed_at,
        },
    }


def _graph_revision(
    *,
    snapshot_revision: int,
    filter_fingerprint: str,
    node_ids: Sequence[str],
    nodes: Sequence[Mapping[str, Any]],
    edge_ids: Sequence[str],
    edges: Sequence[Mapping[str, Any]],
    node_limit: int,
    edge_limit: int,
    authorization_revision: str,
) -> str:
    payload = json.dumps(
        {
            "relation_version": GRAPH_RELATION_VERSION,
            "snapshot_revision": snapshot_revision,
            "filter_fingerprint": filter_fingerprint,
            "authorization_revision": authorization_revision,
            "node_ids": list(node_ids),
            "nodes": list(nodes),
            "edge_ids": list(edge_ids),
            "edges": list(edges),
            "node_limit": node_limit,
            "edge_limit": edge_limit,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"graph-{hashlib.sha256(payload.encode()).hexdigest()[:32]}"


def _edge_observed_at(*resources: Mapping[str, Any]) -> str | None:
    observed = sorted(
        value
        for resource in resources
        if (value := _optional_text(resource.get("observed_at"))) is not None
    )
    return observed[-1] if observed else None


def _historical(*resources: Mapping[str, Any]) -> bool:
    return any(_optional_text(resource.get("deleted_at")) is not None for resource in resources)


def _require_single_cluster(resources: Sequence[Mapping[str, Any]] | Any) -> None:
    cluster_ids = {
        str(resource.get("cluster_id") or "")
        for resource in resources
        if str(resource.get("cluster_id") or "")
    }
    if len(cluster_ids) > 1:
        raise ValueError("graph resources must belong to one cluster")


def _inventory_key(item: Mapping[str, Any]) -> str:
    key = str(_mapping(item.get("resource")).get("inventory_key") or "")
    if not key:
        raise ValueError("graph resource is missing inventory_key")
    return key


def _unique(values: Sequence[str]) -> str | None:
    return values[0] if len(values) == 1 else None


def _mapping(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_mapping(value: Any) -> dict[str, str]:
    return {str(key): str(item) for key, item in _mapping(value).items()}


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
