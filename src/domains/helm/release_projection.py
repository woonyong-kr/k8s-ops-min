"""Pure projection from inventory metadata to Helm release contracts."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from domains.target.connectivity import (
    AGENT_STATUS_ONLINE,
    AGENT_STATUS_STALE,
    cluster_connection_status,
)
from packages.config.refresh_policies import (
    integral_refresh_after_seconds,
    post_mutation_refresh_after_seconds,
)
from packages.contracts.helm.releases import (
    HelmFeatureAvailability,
    HelmObservationCoverage,
    HelmOwnedResource,
    HelmOwnedResourceObservation,
    HelmRelease,
    HelmReleaseDetail,
    HelmReleaseDetailResponse,
    HelmReleaseHistoryEntry,
    HelmReleaseListResponse,
    HelmResourceHealthAvailability,
    HelmResourceHealthObservation,
)
from packages.contracts.parity import ClusterScope, ResourceRef
from packages.storage.engine import iso_or_none

HELM_STORAGE_OWNER_LABEL = "owner"
HELM_STORAGE_OWNER_VALUE = "helm"
HELM_RELEASE_NAME_LABEL = "name"
HELM_RELEASE_REVISION_LABEL = "version"
HELM_RELEASE_STATUS_LABEL = "status"
HELM_STORAGE_KINDS = frozenset({"secret", "configmap"})

_RESOURCE_HEALTH_UNAVAILABLE = "owned_resources_not_correlated"
_RESOURCE_SNAPSHOT_UNAVAILABLE = "owned_resources_snapshot_unavailable"
_RESOURCE_TRUNCATED = "helm_owned_resources_truncated"
_MANIFEST_UNAVAILABLE = "helm_manifest_provider_not_integrated"
_VALUES_UNAVAILABLE = "helm_values_provider_not_integrated"
_COMMANDS_UNAVAILABLE = "agent_helm_executor_not_integrated"
_CHART_IDENTITY_UNAVAILABLE = "helm_chart_identity_unavailable"
_CHART_IDENTITY_AMBIGUOUS = "helm_chart_identity_ambiguous"
_CHART_IDENTITY_INVALID = "helm_chart_identity_invalid"
_SEMVER_SUFFIX = re.compile(
    r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:_[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True)
class ObservedHelmStorage:
    scope: ClusterScope
    release_name: str
    storage_namespace: str
    storage: ResourceRef
    storage_resource_version: str | None
    revision: int | None
    status: str | None
    observed_at: str | None


@dataclass(frozen=True)
class ObservedHelmOwnedResource:
    cluster_id: str
    release_name: str
    release_namespace: str
    item: HelmOwnedResource


def helm_release_list(
    storage_rows: Sequence[Mapping[str, Any]],
    *,
    contexts: Mapping[str, Mapping[str, Any]],
    agent_statuses: Mapping[str, Mapping[str, Any]],
    selected_cluster_ids: Iterable[str],
    owned_resource_rows: Sequence[Mapping[str, Any]] | None = None,
    owned_resources_truncated: bool = False,
) -> HelmReleaseListResponse:
    """Build a list without treating a storage payload as a Helm API response."""

    selected = tuple(sorted({_text(value) for value in selected_cluster_ids if _text(value)}))
    coverage = helm_observation_coverage(contexts, selected_cluster_ids=selected)
    observed = _observed_rows(storage_rows, contexts, agent_statuses)
    owned_by_release = _owned_resource_index(owned_resource_rows)
    chart_labels_by_release = _chart_label_index(owned_resource_rows)
    latest_by_release: dict[tuple[str, str, str], ObservedHelmStorage] = {}
    for item in observed:
        key = (item.scope.cluster_id, item.storage_namespace, item.release_name)
        current = latest_by_release.get(key)
        if current is None or _observation_order(item) > _observation_order(current):
            latest_by_release[key] = item
    releases = tuple(
        _release(
            item,
            owned_resources=owned_by_release.get(
                (item.scope.cluster_id, item.storage_namespace, item.release_name),
                (),
            )
            if owned_resource_rows is not None
            else None,
            context=contexts.get(item.scope.cluster_id),
            owned_resources_truncated=owned_resources_truncated,
            chart_labels=chart_labels_by_release.get(
                (item.scope.cluster_id, item.storage_namespace, item.release_name),
                (),
            )
            if owned_resource_rows is not None
            else None,
        )
        for _key, item in sorted(
            latest_by_release.items(),
            key=lambda pair: (pair[0][0], pair[0][1], pair[0][2]),
        )
    )
    return HelmReleaseListResponse(
        releases=releases,
        coverage=coverage,
        refresh_after_seconds=integral_refresh_after_seconds("helm_list"),
        post_mutation_refresh_after_seconds=post_mutation_refresh_after_seconds("helm_list"),
    )


def helm_release_detail(
    storage_rows: Sequence[Mapping[str, Any]],
    *,
    contexts: Mapping[str, Mapping[str, Any]],
    agent_statuses: Mapping[str, Mapping[str, Any]],
    selected_cluster_id: str,
    namespace: str,
    release_name: str,
    owned_resource_rows: Sequence[Mapping[str, Any]] | None = None,
    owned_resources_truncated: bool = False,
) -> HelmReleaseDetailResponse | None:
    """Return safe detail metadata for one exact scope, or no release at all."""

    cluster = _text(selected_cluster_id)
    selected_namespace = _text(namespace)
    selected_name = _text(release_name)
    observed = [
        item
        for item in _observed_rows(storage_rows, contexts, agent_statuses)
        if item.scope.cluster_id == cluster
        and item.storage_namespace == selected_namespace
        and item.release_name == selected_name
    ]
    if not observed:
        return None
    latest = max(observed, key=_observation_order)
    owned_resources = (
        _owned_resource_index(owned_resource_rows).get(
            (cluster, selected_namespace, selected_name),
            (),
        )
        if owned_resource_rows is not None
        else None
    )
    chart_labels = (
        _chart_label_index(owned_resource_rows).get(
            (cluster, selected_namespace, selected_name),
            (),
        )
        if owned_resource_rows is not None
        else None
    )
    context = contexts.get(cluster)
    history = tuple(
        HelmReleaseHistoryEntry(
            storage=item.storage,
            revision=item.revision,
            status=item.status,
            observed_at=item.observed_at,
        )
        for item in sorted(observed, key=_observation_order, reverse=True)
    )
    return HelmReleaseDetailResponse(
        detail=HelmReleaseDetail(
            release=_release(
                latest,
                owned_resources=owned_resources,
                context=context,
                owned_resources_truncated=owned_resources_truncated,
                chart_labels=chart_labels,
            ),
            history=history,
            manifest=HelmFeatureAvailability(reason_code=_MANIFEST_UNAVAILABLE),
            values=HelmFeatureAvailability(reason_code=_VALUES_UNAVAILABLE),
            owned_resources=_owned_resource_observation(
                owned_resources,
                context=context,
                truncated=owned_resources_truncated,
            ),
            commands=HelmFeatureAvailability(reason_code=_COMMANDS_UNAVAILABLE),
        ),
        refresh_after_seconds=integral_refresh_after_seconds("helm_detail"),
        post_mutation_refresh_after_seconds=post_mutation_refresh_after_seconds("helm_detail"),
    )


def helm_observation_coverage(
    contexts: Mapping[str, Mapping[str, Any]],
    *,
    selected_cluster_ids: Iterable[str],
) -> HelmObservationCoverage:
    """State whether an empty list can truthfully mean no Helm releases."""

    selected = tuple(sorted({_text(value) for value in selected_cluster_ids if _text(value)}))
    if not selected:
        return HelmObservationCoverage(
            availability="unavailable",
            reason_codes=("authorization_scope_empty",),
        )
    normalized = {cluster_id: contexts.get(cluster_id) for cluster_id in selected}
    missing = [cluster_id for cluster_id, context in normalized.items() if not context]
    reasons: set[str] = set()
    observed_at: list[str] = []
    for cluster_id, context in normalized.items():
        if not context:
            reasons.add(f"inventory_snapshot_unavailable:{cluster_id}")
            continue
        if int(context.get("snapshot_revision") or 0) <= 0:
            reasons.add(f"inventory_snapshot_unavailable:{cluster_id}")
        if not bool(context.get("resources_complete")):
            reasons.add("source_resources_incomplete")
        if not bool(context.get("labels_complete")):
            reasons.add("helm_storage_labels_incomplete")
        reasons.update(
            _text(value) for value in context.get("partial_reason_codes", ()) if _text(value)
        )
        if stamp := _optional_text(context.get("observed_at")):
            observed_at.append(stamp)
    if missing or any(reason.startswith("inventory_snapshot_unavailable:") for reason in reasons):
        availability = "unavailable"
    elif reasons:
        availability = "partial"
    else:
        availability = "available"
    return HelmObservationCoverage(
        availability=availability,
        observed_at=max(observed_at) if observed_at else None,
        reason_codes=tuple(sorted(reasons)),
    )


def _observed_rows(
    storage_rows: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    agent_statuses: Mapping[str, Mapping[str, Any]],
) -> tuple[ObservedHelmStorage, ...]:
    return tuple(
        observed
        for row in storage_rows
        if (observed := _observed_storage(row, contexts, agent_statuses)) is not None
    )


def _observed_storage(
    row: Mapping[str, Any],
    contexts: Mapping[str, Mapping[str, Any]],
    agent_statuses: Mapping[str, Mapping[str, Any]],
) -> ObservedHelmStorage | None:
    labels = _string_mapping(row.get("labels"))
    if labels.get(HELM_STORAGE_OWNER_LABEL, "").casefold() != HELM_STORAGE_OWNER_VALUE:
        return None
    kind = _text(row.get("kind"))
    if kind.casefold() not in HELM_STORAGE_KINDS:
        return None
    workspace_id = _text(row.get("workspace_id"))
    cluster_id = _text(row.get("cluster_id"))
    namespace = _text(row.get("namespace"))
    release_name = _optional_text(labels.get(HELM_RELEASE_NAME_LABEL))
    inventory_key = _text(row.get("inventory_key"))
    if not all((workspace_id, cluster_id, namespace, release_name, inventory_key)):
        return None
    api_group, version = _api_group_and_version(_text(row.get("api_version")))
    context = contexts.get(cluster_id, {})
    return ObservedHelmStorage(
        scope=ClusterScope(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            namespaces=(namespace,),
            freshness=_freshness(context, agent_statuses.get(cluster_id)),
        ),
        release_name=release_name,
        storage_namespace=namespace,
        storage=ResourceRef(
            api_group=api_group,
            version=version,
            kind=kind,
            namespace=namespace,
            name=_text(row.get("name")) or inventory_key,
            uid=_optional_text(row.get("uid")) or inventory_key,
        ),
        storage_resource_version=_optional_text(row.get("resource_version")),
        revision=_revision(labels.get(HELM_RELEASE_REVISION_LABEL)),
        status=_optional_text(labels.get(HELM_RELEASE_STATUS_LABEL)),
        observed_at=_iso(row.get("observed_at")),
    )


def _release(
    item: ObservedHelmStorage,
    *,
    owned_resources: Sequence[HelmOwnedResource] | None,
    context: Mapping[str, Any] | None,
    owned_resources_truncated: bool,
    chart_labels: Sequence[str] | None,
) -> HelmRelease:
    chart, chart_version, chart_reasons = _chart_identity(
        chart_labels,
        context=context,
        truncated=owned_resources_truncated,
    )
    return HelmRelease(
        scope=item.scope,
        name=item.release_name,
        storage_namespace=item.storage_namespace,
        storage=item.storage,
        storage_resource_version=item.storage_resource_version,
        chart=chart,
        chart_version=chart_version,
        chart_reason_codes=chart_reasons,
        status=item.status,
        revision=item.revision,
        observed_at=item.observed_at,
        resource_health=_resource_health_observation(
            owned_resources,
            context=context,
            truncated=owned_resources_truncated,
        ),
    )


def _chart_label_index(
    rows: Sequence[Mapping[str, Any]] | None,
) -> dict[tuple[str, str, str], tuple[str, ...]]:
    if rows is None:
        return {}
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for row in rows:
        observed = _observed_owned_resource(row)
        if observed is None:
            continue
        label = _optional_text(row.get("chart_label"))
        if label:
            grouped.setdefault(
                (
                    observed.cluster_id,
                    observed.release_namespace,
                    observed.release_name,
                ),
                [],
            ).append(label)
    return {key: tuple(values) for key, values in grouped.items()}


def _chart_identity(
    labels: Sequence[str] | None,
    *,
    context: Mapping[str, Any] | None,
    truncated: bool,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    if labels is None or not _context_observed(context):
        return None, None, (_CHART_IDENTITY_UNAVAILABLE,)
    incomplete = _owned_resource_reason_codes(context, truncated=truncated)
    if incomplete:
        return None, None, tuple(sorted({_CHART_IDENTITY_UNAVAILABLE, *incomplete}))
    if not labels:
        return None, None, (_CHART_IDENTITY_UNAVAILABLE,)
    parsed = tuple(_parse_chart_label(label) for label in labels)
    if any(item is None for item in parsed):
        return None, None, (_CHART_IDENTITY_INVALID,)
    identities = {item for item in parsed if item is not None}
    if len(identities) != 1:
        return None, None, (_CHART_IDENTITY_AMBIGUOUS,)
    chart, version = next(iter(identities))
    return chart, version, ()


def _parse_chart_label(value: str) -> tuple[str, str] | None:
    parts = value.strip().split("-")
    candidates: set[tuple[str, str]] = set()
    for index in range(1, len(parts)):
        chart = "-".join(parts[:index]).strip()
        version = "-".join(parts[index:]).strip()
        if chart and _SEMVER_SUFFIX.fullmatch(version):
            candidates.add((chart, version.replace("_", "+")))
    return next(iter(candidates)) if len(candidates) == 1 else None


def _owned_resource_index(
    rows: Sequence[Mapping[str, Any]] | None,
) -> dict[tuple[str, str, str], tuple[HelmOwnedResource, ...]]:
    if rows is None:
        return {}
    grouped: dict[tuple[str, str, str], list[HelmOwnedResource]] = {}
    for row in rows:
        observed = _observed_owned_resource(row)
        if observed is None:
            continue
        key = (
            observed.cluster_id,
            observed.release_namespace,
            observed.release_name,
        )
        grouped.setdefault(key, []).append(observed.item)
    return {
        key: tuple(
            sorted(
                items,
                key=lambda item: (
                    item.resource.kind.casefold(),
                    item.resource.namespace or "",
                    item.resource.name.casefold(),
                    item.resource.uid,
                ),
            )
        )
        for key, items in grouped.items()
    }


def _observed_owned_resource(row: Mapping[str, Any]) -> ObservedHelmOwnedResource | None:
    workspace_id = _text(row.get("workspace_id"))
    cluster_id = _text(row.get("cluster_id"))
    inventory_key = _text(row.get("inventory_key"))
    release_name = _text(row.get("release_name"))
    release_namespace = _text(row.get("release_namespace"))
    namespace = _text(row.get("namespace"))
    kind = _text(row.get("kind"))
    name = _text(row.get("name"))
    if (
        not all(
            (
                workspace_id,
                cluster_id,
                inventory_key,
                release_name,
                release_namespace,
                namespace,
                kind,
                name,
            )
        )
        or release_namespace != namespace
    ):
        return None
    api_group, version = _api_group_and_version(_text(row.get("api_version")))
    return ObservedHelmOwnedResource(
        cluster_id=cluster_id,
        release_name=release_name,
        release_namespace=release_namespace,
        item=HelmOwnedResource(
            resource=ResourceRef(
                api_group=api_group,
                version=version,
                kind=kind,
                namespace=namespace,
                name=name,
                uid=_optional_text(row.get("uid")) or inventory_key,
            ),
            status=_text(row.get("status")) or "unknown",
            health=_text(row.get("health")) or "unknown",
            observed_at=_iso(row.get("observed_at")),
        ),
    )


def _owned_resource_observation(
    resources: Sequence[HelmOwnedResource] | None,
    *,
    context: Mapping[str, Any] | None,
    truncated: bool,
) -> HelmOwnedResourceObservation | HelmFeatureAvailability:
    if resources is None:
        return HelmFeatureAvailability(reason_code=_RESOURCE_HEALTH_UNAVAILABLE)
    if not _context_observed(context):
        return HelmFeatureAvailability(reason_code=_RESOURCE_SNAPSHOT_UNAVAILABLE)
    reasons = _owned_resource_reason_codes(context, truncated=truncated)
    return HelmOwnedResourceObservation(
        availability="partial" if reasons else "available",
        items=tuple(resources),
        observed_at=_latest_resource_observation(resources, context=context),
        truncated=truncated,
        reason_codes=reasons,
    )


def _resource_health_observation(
    resources: Sequence[HelmOwnedResource] | None,
    *,
    context: Mapping[str, Any] | None,
    truncated: bool,
) -> HelmResourceHealthObservation | HelmResourceHealthAvailability:
    if resources is None:
        return HelmResourceHealthAvailability(reason_code=_RESOURCE_HEALTH_UNAVAILABLE)
    if not _context_observed(context):
        return HelmResourceHealthAvailability(reason_code=_RESOURCE_SNAPSHOT_UNAVAILABLE)
    reasons = _owned_resource_reason_codes(context, truncated=truncated)
    return HelmResourceHealthObservation(
        availability="partial" if reasons else "available",
        health=_aggregate_resource_health(resources),
        resource_count=len(resources),
        observed_at=_latest_resource_observation(resources, context=context),
        reason_codes=reasons,
    )


def _context_observed(context: Mapping[str, Any] | None) -> bool:
    return bool(context) and int(context.get("snapshot_revision") or 0) > 0


def _owned_resource_reason_codes(
    context: Mapping[str, Any] | None,
    *,
    truncated: bool,
) -> tuple[str, ...]:
    reasons = {
        _text(reason) for reason in (context or {}).get("partial_reason_codes", ()) if _text(reason)
    }
    if not bool((context or {}).get("resources_complete")):
        reasons.add("source_resources_incomplete")
    if not bool((context or {}).get("labels_complete")):
        reasons.add("helm_ownership_labels_incomplete")
    if truncated:
        reasons.add(_RESOURCE_TRUNCATED)
    return tuple(sorted(reasons))


def _aggregate_resource_health(resources: Sequence[HelmOwnedResource]) -> str:
    counts = Counter(item.health.strip().casefold() or "unknown" for item in resources)
    if not counts:
        return "unknown"
    if "degraded" in counts:
        return "degraded"
    if len(counts) == 1:
        return next(iter(counts))
    return "mixed"


def _latest_resource_observation(
    resources: Sequence[HelmOwnedResource],
    *,
    context: Mapping[str, Any] | None,
) -> str | None:
    observations = [item.observed_at for item in resources if item.observed_at]
    if observations:
        return max(observations)
    return _optional_text((context or {}).get("observed_at"))


def _freshness(context: Mapping[str, Any], agent: Mapping[str, Any] | None) -> str:
    """Combine authoritative heartbeat liveness with inventory completeness.

    A complete inventory snapshot is evidence of what was observed, not proof that
    the agent is still connected.  Connection states therefore take precedence;
    only an online agent can make a complete observation ``live``.
    """

    connection = cluster_connection_status(agent)
    if connection == AGENT_STATUS_STALE:
        return "stale"
    if connection != AGENT_STATUS_ONLINE:
        return "disconnected"
    if int(context.get("snapshot_revision") or 0) <= 0:
        return "disconnected"
    if not bool(context.get("resources_complete")) or not bool(context.get("labels_complete")):
        return "partial"
    return "live"


def _observation_order(item: ObservedHelmStorage) -> tuple[int, str, str]:
    return (item.revision or 0, item.observed_at or "", item.storage.uid)


def _revision(value: object) -> int | None:
    try:
        parsed = int(_text(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _api_group_and_version(api_version: str) -> tuple[str, str]:
    group, separator, version = api_version.partition("/")
    return (group, version) if separator else ("", group)


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {_text(key): _text(item) for key, item in value.items() if _text(key)}


def _iso(value: object) -> str | None:
    return iso_or_none(value) or _optional_text(value)


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_text(value: object) -> str | None:
    return _text(value) or None
