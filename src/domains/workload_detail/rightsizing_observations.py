"""Rightsizing from durable cluster-agent inventory and usage observations."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from domains.inventory.container_metrics import container_metric_observations
from packages.contracts.parity import ResourceRef
from packages.contracts.rightsizing import (
    RightsizingImpact,
    RightsizingMetricRecommendation,
    RightsizingObservedWorkload,
    RightsizingProvenance,
    RightsizingQuantity,
)
from packages.kubernetes_quantity import cpu_millicores, memory_mebibytes

RIGHTSIZING_ALGORITHM_REVISION = "agent-history-rightsizing/v1"
RIGHTSIZING_COLLECTOR = "cluster-agent"
RIGHTSIZING_SOURCE_REVISION = "agent-usage/v1"
RIGHTSIZING_WINDOW = timedelta(days=7)
RIGHTSIZING_SAMPLE_INTERVAL_SECONDS = 300
RIGHTSIZING_EXPECTED_SAMPLES = (
    int(RIGHTSIZING_WINDOW.total_seconds() / RIGHTSIZING_SAMPLE_INTERVAL_SECONDS) + 1
)
RIGHTSIZING_MIN_SAMPLES = 72
RIGHTSIZING_HEADROOM = 1.15
RIGHTSIZING_MEDIUM_COVERAGE_BPS = 1_400
MEBIBYTE = 1024 * 1024
RIGHTSIZING_MEMORY_MIN_BYTES = 64 * MEBIBYTE
RIGHTSIZING_CPU_MIN_MILLICORES = 10
SUPPORTED_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet"})

MetricResource = Literal["cpu", "memory"]


@dataclass(frozen=True)
class RightsizingWorkloadProjection:
    observation: RightsizingObservedWorkload | None
    reason_code: str | None
    has_data: bool


@dataclass(frozen=True)
class _ContainerSpec:
    name: str
    cpu_request_millicores: int | None
    memory_request_bytes: int | None


def project_rightsizing_workload(
    workload: Mapping[str, Any],
    *,
    dependents: Sequence[Mapping[str, Any]],
    usage_samples: Sequence[Mapping[str, Any]],
    snapshot_complete: bool,
) -> RightsizingWorkloadProjection:
    """Project one workload without querying a target or inferring ownership from labels."""
    if not snapshot_complete:
        return _failure("inventory_snapshot_incomplete")
    kind = _text(workload.get("kind"))
    if kind not in SUPPORTED_KINDS:
        return _failure("rightsizing_workload_kind_not_supported")
    resource = _resource_ref(workload)
    snapshot_id = _text(workload.get("snapshot_id"))
    if resource is None or not snapshot_id:
        return _failure("workload_identity_incomplete")
    containers = _container_specs(workload)
    if not containers:
        return _failure("workload_container_spec_unavailable")
    owned_pods = _owned_pods(
        workload,
        dependents=dependents,
        snapshot_id=snapshot_id,
    )
    replicas = _replicas(workload)
    scaled_to_zero = replicas == 0
    if owned_pods is None or (not owned_pods and not scaled_to_zero):
        return _failure("workload_pod_ownership_incomplete")

    samples = _bucketed_container_samples(
        usage_samples,
        pods=owned_pods,
        container_names=tuple(container.name for container in containers),
    )
    ended_at = _window_end(usage_samples, workload)
    provenance = rightsizing_provenance(
        snapshot_id=snapshot_id,
        ended_at=ended_at,
        usage_samples=usage_samples,
    )
    rows = tuple(
        _metric_recommendation(
            container,
            resource_name,
            values=tuple(
                value
                for sample in samples
                if (value := sample.get((container.name, resource_name))) is not None
            ),
        )
        for container in containers
        for resource_name in ("cpu", "memory")
    )
    classification = _classification(rows)
    impact = _impact(rows, replicas=replicas, scaled_to_zero=scaled_to_zero)
    has_data = any(row.observed_demand is not None for row in rows)
    observation = RightsizingObservedWorkload(
        availability="partial",
        resource=resource,
        observed_at=_iso(ended_at),
        freshness="partial",
        provenance=provenance,
        replicas=replicas,
        scaled_to_zero=scaled_to_zero,
        classification=classification,
        impact=impact,
        rows=rows,
        reason_codes=("current_pod_ownership_only",),
    )
    return RightsizingWorkloadProjection(
        observation=observation,
        reason_code=None,
        has_data=has_data,
    )


def rightsizing_provenance(
    *,
    snapshot_id: str,
    ended_at: datetime,
    usage_samples: Sequence[Mapping[str, Any]],
) -> RightsizingProvenance:
    """Build the stable provenance shared by detail and fleet scan projections."""
    return RightsizingProvenance(
        collector=RIGHTSIZING_COLLECTOR,
        algorithm_revision=RIGHTSIZING_ALGORITHM_REVISION,
        source_revision=_source_revision(snapshot_id, usage_samples),
        window_started_at=_iso(ended_at - RIGHTSIZING_WINDOW),
        window_ended_at=_iso(ended_at),
        sample_interval_seconds=RIGHTSIZING_SAMPLE_INTERVAL_SECONDS,
    )


def _failure(reason_code: str) -> RightsizingWorkloadProjection:
    return RightsizingWorkloadProjection(
        observation=None,
        reason_code=reason_code,
        has_data=False,
    )


def _resource_ref(workload: Mapping[str, Any]) -> ResourceRef | None:
    api_version = _text(workload.get("api_version"))
    kind = _text(workload.get("kind"))
    name = _text(workload.get("name"))
    uid = _text(workload.get("uid"))
    if not api_version or not kind or not name or not uid:
        return None
    api_group, separator, version = api_version.partition("/")
    if not separator:
        api_group, version = "", api_group
    namespace = _text(workload.get("namespace")) or None
    return ResourceRef(
        api_group=api_group,
        version=version,
        kind=kind,
        namespace=namespace,
        name=name,
        uid=uid,
    )


def _container_specs(workload: Mapping[str, Any]) -> tuple[_ContainerSpec, ...]:
    summary = _mapping(workload.get("summary"))
    template = _mapping(summary.get("pod_template"))
    spec = _mapping(template.get("spec"))
    raw_containers = spec.get("containers")
    if not isinstance(raw_containers, list):
        return ()
    containers: dict[str, _ContainerSpec] = {}
    for raw in raw_containers:
        if not isinstance(raw, Mapping):
            return ()
        name = _text(raw.get("name"))
        if not name or name in containers:
            return ()
        resources = _mapping(raw.get("resources"))
        requests = _mapping(resources.get("requests"))
        cpu = cpu_millicores(requests.get("cpu"))
        memory = memory_mebibytes(requests.get("memory"))
        containers[name] = _ContainerSpec(
            name=name,
            cpu_request_millicores=_positive_int(cpu),
            memory_request_bytes=_positive_int(memory * MEBIBYTE if memory is not None else None),
        )
    return tuple(containers[name] for name in sorted(containers))


def _owned_pods(
    workload: Mapping[str, Any],
    *,
    dependents: Sequence[Mapping[str, Any]],
    snapshot_id: str,
) -> tuple[Mapping[str, Any], ...] | None:
    workload_uid = _text(workload.get("uid"))
    kind = _text(workload.get("kind"))
    if not workload_uid:
        return None
    current = [item for item in dependents if _text(item.get("snapshot_id")) == snapshot_id]
    owner_uids = {workload_uid}
    if kind == "Deployment":
        revisions = [
            item
            for item in current
            if _text(item.get("resource_type")) == "workload_revision"
            and _text(item.get("kind")) == "ReplicaSet"
            and _text(_mapping(item.get("summary")).get("owner_uid")) == workload_uid
        ]
        if not revisions or any(not _owner_complete(item) for item in revisions):
            return None
        owner_uids = {_text(item.get("uid")) for item in revisions if _text(item.get("uid"))}
        if not owner_uids:
            return None
    pods = [
        item
        for item in current
        if _text(item.get("resource_type")) == "pod"
        and _text(_mapping(item.get("summary")).get("owner_uid")) in owner_uids
    ]
    if any(not _owner_complete(item) for item in pods):
        return None
    return tuple(
        sorted(pods, key=lambda item: (_text(item.get("namespace")), _text(item.get("name"))))
    )


def _owner_complete(item: Mapping[str, Any]) -> bool:
    return _mapping(item.get("summary")).get("owner_references_complete") is True


def _bucketed_container_samples(
    usage_samples: Sequence[Mapping[str, Any]],
    *,
    pods: Sequence[Mapping[str, Any]],
    container_names: tuple[str, ...],
) -> tuple[dict[tuple[str, MetricResource], float], ...]:
    buckets: dict[int, tuple[datetime, dict[tuple[str, MetricResource], float]]] = {}
    for sample in usage_samples:
        sampled_at = _timestamp(sample.get("sampled_at"))
        if sampled_at is None:
            continue
        values = _sample_container_maxima(
            sample,
            pods=pods,
            container_names=container_names,
        )
        bucket = int(sampled_at.timestamp()) // RIGHTSIZING_SAMPLE_INTERVAL_SECONDS
        current = buckets.get(bucket)
        if current is None or sampled_at >= current[0]:
            buckets[bucket] = (sampled_at, values)
    return tuple(buckets[key][1] for key in sorted(buckets))


def _sample_container_maxima(
    sample: Mapping[str, Any],
    *,
    pods: Sequence[Mapping[str, Any]],
    container_names: tuple[str, ...],
) -> dict[tuple[str, MetricResource], float]:
    if not pods:
        return {}
    pod_usage = _mapping(_mapping(sample.get("usage")).get("pods"))
    per_container: dict[str, list[Mapping[str, Any]]] = {name: [] for name in container_names}
    for pod in pods:
        namespace = _text(pod.get("namespace")) or "default"
        name = _text(pod.get("name"))
        uid = _text(pod.get("uid"))
        measured = _mapping(pod_usage.get(f"{namespace}/{name}"))
        raw_metrics = measured.get("container_metrics")
        metrics = container_metric_observations(raw_metrics)
        if (
            not uid
            or _text(measured.get("uid")) != uid
            or measured.get("container_metrics_complete") is not True
            or not isinstance(raw_metrics, list)
            or len(metrics) != len(raw_metrics)
        ):
            return {}
        by_name = {_text(item.get("name")): item for item in metrics}
        if any(container_name not in by_name for container_name in container_names):
            return {}
        for container_name in container_names:
            per_container[container_name].append(by_name[container_name])
    values: dict[tuple[str, MetricResource], float] = {}
    for container_name, observations in per_container.items():
        cpu = [
            float(item["cpu_mcores"]) for item in observations if item.get("cpu_mcores") is not None
        ]
        memory = [
            float(item["mem_mib"]) for item in observations if item.get("mem_mib") is not None
        ]
        if len(cpu) == len(pods):
            values[(container_name, "cpu")] = max(cpu)
        if len(memory) == len(pods):
            values[(container_name, "memory")] = max(memory) * MEBIBYTE
    return values


def _metric_recommendation(
    container: _ContainerSpec,
    resource: MetricResource,
    *,
    values: tuple[float, ...],
) -> RightsizingMetricRecommendation:
    current_value = (
        container.cpu_request_millicores if resource == "cpu" else container.memory_request_bytes
    )
    unit = "millicores" if resource == "cpu" else "bytes"
    current = RightsizingQuantity(unit=unit, value=current_value) if current_value else None
    sample_count = len(values)
    coverage = min(10_000, round(sample_count / RIGHTSIZING_EXPECTED_SAMPLES * 10_000))
    confidence: Literal["high", "medium", "low", "none"] = "low"
    if coverage >= RIGHTSIZING_MEDIUM_COVERAGE_BPS and sample_count >= RIGHTSIZING_MIN_SAMPLES:
        confidence = "medium"
    if sample_count < RIGHTSIZING_MIN_SAMPLES:
        observed_value = _observed_value(values, resource) if values else None
        return RightsizingMetricRecommendation(
            container=container.name,
            resource=resource,
            fit="insufficient_history",
            action="need_data",
            confidence=confidence,
            current_request=current,
            observed_demand=(
                RightsizingQuantity(unit=unit, value=observed_value)
                if observed_value is not None
                else None
            ),
            recommended_request=None,
            sample_count=sample_count,
            expected_samples=RIGHTSIZING_EXPECTED_SAMPLES,
            coverage_basis_points=coverage,
            signals=("history_incomplete",),
            reason_codes=("insufficient_history",),
        )

    observed_value = _observed_value(values, resource)
    candidate = _recommended_value(observed_value, resource)
    signals: tuple[str, ...] = ()
    if resource == "cpu" and _is_bursty(values):
        signals = ("bursty",)
    if current_value is None:
        fit, action, recommendation, reasons = (
            "missing_request",
            "increase",
            candidate,
            ("request_missing",),
        )
    elif candidate <= current_value * 0.7:
        fit, action, recommendation = "oversized", "review", None
        reasons = ("hpa_evidence_unavailable" if resource == "cpu" else "oom_evidence_unavailable",)
    elif candidate > current_value:
        fit, action, recommendation, reasons = (
            "under_requested",
            "increase",
            candidate,
            ("observed_demand_exceeds_request",),
        )
    else:
        fit, action, recommendation, reasons = (
            "balanced",
            "in_range",
            None,
            ("request_within_fit_range",),
        )
    return RightsizingMetricRecommendation(
        container=container.name,
        resource=resource,
        fit=fit,
        action=action,
        confidence=confidence,
        current_request=current,
        observed_demand=RightsizingQuantity(unit=unit, value=observed_value),
        recommended_request=(
            RightsizingQuantity(unit=unit, value=recommendation)
            if recommendation is not None
            else None
        ),
        sample_count=sample_count,
        expected_samples=RIGHTSIZING_EXPECTED_SAMPLES,
        coverage_basis_points=coverage,
        signals=signals,
        reason_codes=reasons,
    )


def _observed_value(values: tuple[float, ...], resource: MetricResource) -> int:
    if resource == "memory":
        return math.ceil(max(values))
    return math.ceil(_percentile(values, 0.95))


def _recommended_value(observed: int, resource: MetricResource) -> int:
    candidate = observed * RIGHTSIZING_HEADROOM
    if resource == "cpu":
        millicores = max(RIGHTSIZING_CPU_MIN_MILLICORES, math.ceil(candidate))
        step = (
            10
            if millicores < 100
            else 50
            if millicores < 1000
            else 500
            if millicores < 4000
            else 1000
        )
        return _round_up(millicores, step)
    mib = math.ceil(max(candidate, RIGHTSIZING_MEMORY_MIN_BYTES) / MEBIBYTE)
    preferred = (
        64,
        96,
        128,
        192,
        256,
        384,
        512,
        768,
        1024,
        1536,
        2048,
        3072,
        4096,
        6144,
        8192,
    )
    selected = next((value for value in preferred if mib <= value), None)
    if selected is None:
        selected = _round_up(mib, 2048)
    return selected * MEBIBYTE


def _is_bursty(values: tuple[float, ...]) -> bool:
    p95 = _percentile(values, 0.95)
    p99 = _percentile(values, 0.99)
    return p99 - p95 >= 50 and p99 >= p95 * 3


def _percentile(values: tuple[float, ...], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _classification(rows: tuple[RightsizingMetricRecommendation, ...]) -> str:
    actions = {row.action for row in rows}
    for action in ("increase", "reduction", "review", "need_data", "in_range"):
        if action in actions:
            return action
    return "need_data"


def _impact(
    rows: tuple[RightsizingMetricRecommendation, ...],
    *,
    replicas: int,
    scaled_to_zero: bool,
) -> RightsizingImpact:
    if scaled_to_zero:
        return RightsizingImpact(
            replicas=replicas,
            cpu_millicores_change=0,
            memory_bytes_change=0,
        )
    changes = {"cpu": 0, "memory": 0}
    for row in rows:
        if row.action not in ("increase", "reduction") or row.recommended_request is None:
            continue
        current = row.current_request.value if row.current_request is not None else 0
        changes[row.resource] += row.recommended_request.value - current
    return RightsizingImpact(
        replicas=replicas,
        cpu_millicores_change=changes["cpu"] * replicas,
        memory_bytes_change=changes["memory"] * replicas,
    )


def _replicas(workload: Mapping[str, Any]) -> int:
    value = _mapping(workload.get("summary")).get("desired_replicas")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _source_revision(snapshot_id: str, samples: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(f"{RIGHTSIZING_SOURCE_REVISION}:{snapshot_id}".encode())
    for sample in samples:
        digest.update(f":{sample.get('id', '')}:{sample.get('sampled_at', '')}".encode())
    return f"{RIGHTSIZING_SOURCE_REVISION}:{digest.hexdigest()[:32]}"


def _window_end(samples: Sequence[Mapping[str, Any]], workload: Mapping[str, Any]) -> datetime:
    timestamps = [
        parsed for sample in samples if (parsed := _timestamp(sample.get("sampled_at"))) is not None
    ]
    workload_timestamp = _timestamp(workload.get("observed_at"))
    if timestamps:
        return max(timestamps)
    return workload_timestamp or datetime.now(UTC)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value: float | None) -> int | None:
    if value is None or value <= 0:
        return None
    return math.ceil(value)


def _round_up(value: int, step: int) -> int:
    return (value + step - 1) // step * step
