"""Node cost evidence projection without inferred or fixture-backed prices."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from domains.inventory_filter.snapshot_scope import project_snapshot_scope
from packages.config.refresh_policies import integral_refresh_after_seconds
from packages.contracts.cost.observations import (
    CostNodeCapacity,
    CostNodeItem,
    CostNodePageResponse,
    CostNodePricing,
    CostNodePricingCoverage,
    CostNodeUsage,
    CostScopeCoverage,
)
from packages.contracts.parity import ResourceRef
from packages.kubernetes_quantity import cpu_millicores, memory_mebibytes

NODE_PRICING_UNAVAILABLE = "node_pricing_observation_not_integrated"
NODE_PROVIDER_IDENTITY_UNAVAILABLE = "node_provider_identity_not_observed"
NODE_USAGE_UNAVAILABLE = "node_usage_not_observed"
NAMESPACE_ALLOCATION_UNAVAILABLE = "namespace_allocation_not_observed"


def cost_node_page(
    *,
    workspace_id: str,
    selected_cluster_ids: Iterable[str],
    namespace_refs: Iterable[tuple[str, str]],
    contexts: Mapping[str, Mapping[str, Any]],
    resource_page: Mapping[str, Any],
    metric_page: Mapping[str, Any],
    snapshot_revision: int,
    next_cursor: str | None,
) -> CostNodePageResponse:
    namespaces = tuple(sorted(set(namespace_refs)))
    scope = project_snapshot_scope(
        workspace_id=workspace_id,
        contexts=contexts,
        namespace_refs=namespaces,
        selected_cluster_ids=selected_cluster_ids,
    )
    samples = _latest_samples(metric_page.get("samples_by_cluster"))
    items = tuple(
        projected
        for row in _rows(resource_page.get("items"))
        if (projected := _node_item(row, samples)) is not None
    )
    pricing_reasons = {NODE_PRICING_UNAVAILABLE}
    if namespaces:
        pricing_reasons.add(NAMESPACE_ALLOCATION_UNAVAILABLE)
    return CostNodePageResponse(
        scope_coverage=CostScopeCoverage(
            availability=scope.availability,
            scopes=scope.scopes,
            observed_at=scope.observed_at,
            reason_codes=scope.reason_codes,
        ),
        items=items,
        total=max(0, int(resource_page.get("filtered_count") or 0)),
        count_completeness=_count_completeness(contexts, selected_cluster_ids),
        has_more=bool(resource_page.get("has_more")),
        next_cursor=next_cursor,
        snapshot_revision=max(0, int(snapshot_revision)),
        pricing_coverage=CostNodePricingCoverage(reason_codes=tuple(sorted(pricing_reasons))),
        refresh_after_seconds=integral_refresh_after_seconds("cost_nodes"),
    )


def _node_item(
    row: Mapping[str, Any],
    samples: Mapping[str, Mapping[str, Any]],
) -> CostNodeItem | None:
    resource = _mapping(row.get("resource"))
    cluster = _mapping(row.get("cluster"))
    summary = _mapping(resource.get("summary"))
    labels = _mapping(resource.get("labels"))
    cluster_id = _text(resource.get("cluster_id"))
    name = _text(resource.get("name"))
    uid = _text(resource.get("uid"))
    observed_at = _text(resource.get("observed_at"))
    if not cluster_id or not name or not uid or not observed_at:
        return None
    provider_id = _optional_text(summary.get("provider_id"))
    pricing_reasons = [NODE_PRICING_UNAVAILABLE]
    if provider_id is None:
        pricing_reasons.append(NODE_PROVIDER_IDENTITY_UNAVAILABLE)
    allocatable = _mapping(summary.get("allocatable"))
    sample = samples.get(cluster_id, {})
    usage = _mapping(sample.get("usage"))
    measured = _mapping(_mapping(usage.get("nodes")).get(name))
    return CostNodeItem(
        resource=ResourceRef(
            api_group="",
            version=_text(resource.get("api_version"), "v1").rpartition("/")[2],
            kind="Node",
            namespace=None,
            name=name,
            uid=uid,
        ),
        cluster_id=cluster_id,
        cluster_name=_text(cluster.get("name"), cluster_id),
        provider=_text(cluster.get("provider"), "unknown"),
        provider_id=provider_id,
        instance_type=_label(
            labels,
            "node.kubernetes.io/instance-type",
            "beta.kubernetes.io/instance-type",
        ),
        zone=_label(
            labels,
            "topology.kubernetes.io/zone",
            "failure-domain.beta.kubernetes.io/zone",
        ),
        capacity_type=_label(labels, "karpenter.sh/capacity-type"),
        status=_text(resource.get("status"), "Unknown"),
        observed_at=observed_at,
        capacity=CostNodeCapacity(
            cpu_mcores=cpu_millicores(allocatable.get("cpu")),
            memory_mib=memory_mebibytes(allocatable.get("memory")),
            pods=_non_negative_int_or_none(allocatable.get("pods")),
        ),
        usage=_node_usage(sample, measured),
        pricing=CostNodePricing(reason_codes=tuple(pricing_reasons)),
    )


def _node_usage(sample: Mapping[str, Any], measured: Mapping[str, Any]) -> CostNodeUsage:
    observed_at = _optional_text(sample.get("sampled_at"))
    cpu = _number(measured.get("cpu_mcores"))
    memory = _number(_first(measured, "mem_mib", "memory_mib"))
    cpu_percent = _usage_percent(measured, ("cpu_pct", "cpu_percent"), ("cpu_ratio",))
    memory_percent = _usage_percent(
        measured,
        ("mem_pct", "memory_pct"),
        ("mem_ratio", "memory_ratio"),
    )
    values = (cpu, memory, cpu_percent, memory_percent)
    observed = sum(value is not None for value in values)
    if observed_at is None or observed == 0:
        return CostNodeUsage(availability="unavailable", reason_codes=(NODE_USAGE_UNAVAILABLE,))
    if observed == len(values):
        return CostNodeUsage(
            availability="available",
            observed_at=observed_at,
            cpu_mcores=cpu,
            memory_mib=memory,
            cpu_utilization_percent=cpu_percent,
            memory_utilization_percent=memory_percent,
        )
    return CostNodeUsage(
        availability="partial",
        observed_at=observed_at,
        cpu_mcores=cpu,
        memory_mib=memory,
        cpu_utilization_percent=cpu_percent,
        memory_utilization_percent=memory_percent,
        reason_codes=("node_usage_partial",),
    )


def _latest_samples(value: object) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for cluster_id, raw_samples in value.items():
        rows = _rows(raw_samples)
        if rows:
            result[str(cluster_id)] = rows[-1]
    return result


def _count_completeness(
    contexts: Mapping[str, Mapping[str, Any]],
    selected_cluster_ids: Iterable[str],
) -> str:
    selected = tuple(sorted(set(selected_cluster_ids)))
    if not selected or any(
        int((contexts.get(cluster_id) or {}).get("snapshot_revision") or 0) <= 0
        for cluster_id in selected
    ):
        return "unavailable"
    if any(
        not bool((contexts.get(cluster_id) or {}).get("resources_complete"))
        for cluster_id in selected
    ):
        return "partial"
    return "exact"


def _rows(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _label(labels: Mapping[str, Any], *keys: str) -> str | None:
    return next(
        (_optional_text(labels.get(key)) for key in keys if _optional_text(labels.get(key))), None
    )


def _first(values: Mapping[str, Any], *keys: str) -> object:
    return next((values[key] for key in keys if values.get(key) is not None), None)


def _usage_percent(
    measured: Mapping[str, Any],
    percent_keys: tuple[str, ...],
    ratio_keys: tuple[str, ...],
) -> float | None:
    for key in percent_keys:
        if (value := _number(measured.get(key))) is not None:
            return round(value, 2)
    for key in ratio_keys:
        if (value := _number(measured.get(key))) is not None:
            return round(value * 100, 2)
    return None


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _non_negative_int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _text(value: object, default: str = "") -> str:
    normalized = str(value or "").strip()
    return normalized or default


def _optional_text(value: object) -> str | None:
    return _text(value) or None
