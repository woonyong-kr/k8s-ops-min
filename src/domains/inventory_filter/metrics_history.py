"""Build honest sparkline series from persisted inventory usage samples."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from domains.inventory.container_metrics import container_metric_observations
from packages.contracts.event_bus.interfaces import JsonObject

Completeness = Literal["exact", "partial", "unavailable"]


def build_resource_metric_history(
    resources: Sequence[Mapping[str, Any]],
    samples_by_cluster: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    projection_complete: bool,
) -> JsonObject:
    """Return CPU sparkline series without manufacturing missing samples as zero."""
    series: list[JsonObject] = []
    response_reasons: set[str] = set()
    for resource in resources:
        cluster_id = str(resource["cluster_id"])
        resource_type = str(resource.get("resource_type") or "pod")
        if resource_type not in {"node", "pod"}:
            continue
        namespace_value = resource.get("namespace")
        namespace = str(namespace_value) if namespace_value is not None else None
        name = str(resource["name"])
        expected_uid = _non_empty_text(resource.get("uid"))
        usage_key = f"{namespace}/{name}" if resource_type == "pod" else name
        usage_collection = "pods" if resource_type == "pod" else "nodes"
        points: list[JsonObject] = []
        current_observations: list[JsonObject] = []
        container_points: dict[str, list[JsonObject]] = {}
        container_history_reasons: set[str] = set()
        for sample in samples_by_cluster.get(cluster_id, ()):
            observed_at = sample.get("sampled_at")
            if not observed_at:
                continue
            usage = sample.get("usage") if isinstance(sample.get("usage"), dict) else {}
            measurements = (
                usage.get(usage_collection) if isinstance(usage.get(usage_collection), dict) else {}
            )
            measured_value = measurements.get(usage_key)
            measured_present = isinstance(measured_value, dict)
            measured = measured_value if measured_present else {}
            cpu_mcores = _non_negative_number(measured.get("cpu_mcores"))
            mem_mib = _non_negative_number(measured.get("mem_mib", measured.get("memory_mib")))
            points.append(
                {
                    "observed_at": str(observed_at),
                    "cpu_mcores": cpu_mcores,
                    "mem_mib": mem_mib,
                }
            )
            metrics_observed_at = _non_empty_text(measured.get("metrics_observed_at"))
            metrics_window = _non_empty_text(measured.get("metrics_window"))
            raw_container_metrics = measured.get("container_metrics")
            container_metrics = container_metric_observations(raw_container_metrics)
            container_metrics_complete = (
                measured.get("container_metrics_complete") is True
                and isinstance(raw_container_metrics, list)
                and len(container_metrics) == len(raw_container_metrics)
            )
            resource_identity_matches = resource_type != "pod" or (
                expected_uid is not None and _non_empty_text(measured.get("uid")) == expected_uid
            )
            if resource_type == "pod":
                if not measured_present:
                    container_history_reasons.add("container_metrics_history_partial")
                elif not resource_identity_matches:
                    container_history_reasons.add("container_metrics_identity_unavailable")
                elif not container_metrics_complete:
                    container_history_reasons.add("container_metrics_history_partial")
                else:
                    for container in container_metrics:
                        container_points.setdefault(str(container["name"]), []).append(
                            {
                                "observed_at": str(observed_at),
                                "cpu_mcores": container["cpu_mcores"],
                                "mem_mib": container["mem_mib"],
                            }
                        )
            if (
                metrics_observed_at is not None
                and metrics_window is not None
                and (cpu_mcores is not None or mem_mib is not None)
                and resource_identity_matches
            ):
                current_observations.append(
                    {
                        "observed_at": metrics_observed_at,
                        "measurement_window": metrics_window,
                        "cpu_mcores": cpu_mcores,
                        "mem_mib": mem_mib,
                        "containers": container_metrics,
                        "container_metrics_complete": container_metrics_complete,
                    }
                )
        points.sort(key=lambda point: point["observed_at"])
        current_observation = max(
            current_observations,
            key=lambda item: _timestamp_sort_key(str(item["observed_at"])),
            default=None,
        )
        cpu_count = sum(point["cpu_mcores"] is not None for point in points)
        completeness, reasons = _series_completeness(
            point_count=len(points),
            measured_count=cpu_count,
            projection_complete=projection_complete,
        )
        (
            container_history_completeness,
            container_history_reason_codes,
        ) = _container_history_completeness(
            resource_type=resource_type,
            container_points=container_points,
            reasons=container_history_reasons,
            projection_complete=projection_complete,
        )
        container_series = [
            {
                "name": container_name,
                "points": sorted(
                    container_points[container_name],
                    key=lambda point: str(point["observed_at"]),
                ),
                "completeness": container_history_completeness,
                "partial_reason_codes": container_history_reason_codes,
            }
            for container_name in sorted(container_points)
        ]
        response_reasons.update(reasons)
        series.append(
            {
                "resource_id": str(resource["resource_id"]),
                "cluster_id": cluster_id,
                "resource_type": resource_type,
                "namespace": namespace,
                "name": name,
                "points": points,
                "current_observation": current_observation,
                "container_series": container_series,
                "container_history_completeness": container_history_completeness,
                "container_history_reason_codes": container_history_reason_codes,
                "has_sparkline_points": cpu_count > 0,
                "completeness": completeness,
                "partial_reason_codes": reasons,
            }
        )
    response_completeness = _response_completeness(series, projection_complete)
    if not projection_complete:
        response_reasons.add("inventory_projection_partial")
    return {
        "series": series,
        "completeness": response_completeness,
        "partial_reason_codes": sorted(response_reasons),
    }


def _series_completeness(
    *,
    point_count: int,
    measured_count: int,
    projection_complete: bool,
) -> tuple[Completeness, list[str]]:
    if measured_count == 0:
        return "unavailable", ["metrics_history_unavailable"]
    if measured_count < point_count or not projection_complete:
        reasons = ["metrics_history_partial"] if measured_count < point_count else []
        if not projection_complete:
            reasons.append("inventory_projection_partial")
        return "partial", reasons
    return "exact", []


def _response_completeness(
    series: Sequence[Mapping[str, Any]], projection_complete: bool
) -> Completeness:
    values = {str(item.get("completeness")) for item in series}
    if not values or values == {"unavailable"}:
        return "unavailable"
    if projection_complete and values == {"exact"}:
        return "exact"
    return "partial"


def _container_history_completeness(
    *,
    resource_type: str,
    container_points: Mapping[str, Sequence[Mapping[str, Any]]],
    reasons: set[str],
    projection_complete: bool,
) -> tuple[Completeness, list[str]]:
    if resource_type != "pod":
        return "unavailable", ["container_metrics_not_applicable"]
    normalized_reasons = set(reasons)
    if not projection_complete:
        normalized_reasons.add("inventory_projection_partial")
    if not container_points:
        normalized_reasons.add("container_metrics_history_unavailable")
        return "unavailable", sorted(normalized_reasons)
    if normalized_reasons:
        return "partial", sorted(normalized_reasons)
    return "exact", []


def _non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _timestamp_sort_key(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()
