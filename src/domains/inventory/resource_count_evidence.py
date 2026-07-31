"""Project resource-count trust from one persisted cluster-agent observation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from domains.inventory.snapshot_evidence import snapshot_source_summary
from domains.resource_access.projection import (
    ResourceAccessUnavailable,
    agent_execution_access_projection,
)
from packages.contracts.gateway.responses import (
    InventoryResourceCountForbidden,
    InventoryResourceCountsEvidence,
)
from packages.contracts.kubernetes_discovery import ApiResourceDiscoveryObservation

SNAPSHOT_UNAVAILABLE = "inventory_snapshot_evidence_unavailable"
COLLECTION_PARTIAL = "inventory_collection_partial"
SOURCE_RESOURCES_INCOMPLETE = "source_resources_incomplete"
SOURCE_RESOURCES_TRUNCATED = "source_resources_truncated"
DISCOVERY_UNAVAILABLE = "api_resource_discovery_not_observed"
DISCOVERY_INVALID = "api_resource_discovery_invalid"
ACCESS_UNAVAILABLE = "agent_access_evidence_unavailable"


def project_inventory_resource_counts_evidence(
    snapshot: Mapping[str, object] | None,
    *,
    namespace_scope: tuple[str, ...],
) -> InventoryResourceCountsEvidence:
    source = snapshot_source_summary(snapshot)
    if source is None:
        return InventoryResourceCountsEvidence(
            completeness="unavailable",
            namespace_scope=namespace_scope,
            reason_codes=(SNAPSHOT_UNAVAILABLE,),
        )

    reasons: set[str] = set()
    limits = source.get("collection_limits")
    source_truncated = isinstance(limits, Mapping) and limits.get("truncated") is True
    if source_truncated:
        reasons.add(SOURCE_RESOURCES_TRUNCATED)
        reasons.add(COLLECTION_PARTIAL)
    elif source.get("resources_complete") is not True:
        reasons.add(SOURCE_RESOURCES_INCOMPLETE)
        reasons.add(COLLECTION_PARTIAL)

    discovery = _discovery(source, reasons)
    observed_at = discovery.observed_at.isoformat() if discovery is not None else None
    forbidden: dict[tuple[str | None, str, str, str, str], InventoryResourceCountForbidden] = {}
    evaluation_namespaces = namespace_scope or _observed_namespaces(source)
    if discovery is not None:
        reasons.update(discovery.reason_codes)
        if discovery.completeness != "exact" and not discovery.reason_codes:
            reasons.add("api_resource_discovery_incomplete")
        _project_forbidden(
            snapshot,
            evaluation_namespaces=evaluation_namespaces,
            forbidden=forbidden,
            reasons=reasons,
        )

    if observed_at is None:
        return InventoryResourceCountsEvidence(
            completeness="unavailable",
            namespace_scope=namespace_scope,
            reason_codes=tuple(sorted(reasons or {SNAPSHOT_UNAVAILABLE})),
        )
    return InventoryResourceCountsEvidence(
        completeness="partial" if reasons else "observed",
        observed_at=observed_at,
        namespace_scope=namespace_scope,
        reason_codes=tuple(sorted(reasons)),
        forbidden=tuple(forbidden[key] for key in sorted(forbidden, key=_restriction_sort_key)),
    )


def _discovery(
    source: Mapping[str, object],
    reasons: set[str],
) -> ApiResourceDiscoveryObservation | None:
    payload = source.get("api_resource_discovery")
    if not isinstance(payload, Mapping):
        reasons.add(DISCOVERY_UNAVAILABLE)
        return None
    try:
        return ApiResourceDiscoveryObservation.model_validate(payload)
    except ValidationError:
        reasons.add(DISCOVERY_INVALID)
        return None


def _observed_namespaces(source: Mapping[str, object]) -> tuple[str, ...]:
    values = source.get("namespaces")
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        return ()
    return tuple(sorted({value for value in values if isinstance(value, str) and value}))


def _project_forbidden(
    snapshot: Mapping[str, object] | None,
    *,
    evaluation_namespaces: tuple[str, ...],
    forbidden: dict[tuple[str | None, str, str, str, str], InventoryResourceCountForbidden],
    reasons: set[str],
) -> None:
    if not evaluation_namespaces:
        reasons.add(ACCESS_UNAVAILABLE)
        return
    assert snapshot is not None
    for namespace in evaluation_namespaces:
        try:
            evidence = agent_execution_access_projection(snapshot, namespace=namespace)
        except (ResourceAccessUnavailable, ValueError):
            reasons.add(ACCESS_UNAVAILABLE)
            continue
        reasons.update(evidence.reason_codes)
        for item in evidence.restricted_resource_types:
            restriction_namespace = namespace if item.namespaced else None
            key = (
                restriction_namespace,
                item.api_group,
                item.version,
                item.resource,
                item.kind,
            )
            forbidden[key] = InventoryResourceCountForbidden(
                namespace=restriction_namespace,
                api_group=item.api_group,
                version=item.version,
                resource=item.resource,
                kind=item.kind,
                namespaced=item.namespaced,
            )


def _restriction_sort_key(
    key: tuple[str | None, str, str, str, str],
) -> tuple[str, str, str, str, str]:
    namespace, api_group, version, resource, kind = key
    return namespace or "", api_group, version, resource, kind
