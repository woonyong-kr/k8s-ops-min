"""Build snapshot-bound Pod and Node metrics for the existing Resources rows."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject
from packages.kubernetes_quantity import cpu_millicores, memory_mebibytes


def attach_resource_table_metrics(rows: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    """Copy filtered rows and attach bounded metric evidence from that row's snapshot."""
    result: list[JsonObject] = []
    for raw_row in rows:
        row = dict(raw_row)
        resource = raw_row.get("resource")
        row["metrics"] = (
            _resource_table_metrics(resource) if isinstance(resource, Mapping) else None
        )
        result.append(row)
    return result


def _resource_table_metrics(resource: Mapping[str, Any]) -> JsonObject | None:
    resource_type = _text(resource.get("resource_type"))
    if resource_type not in {"pod", "node"}:
        return None
    summary_value = resource.get("summary")
    summary = summary_value if isinstance(summary_value, Mapping) else {}
    common: JsonObject = {
        "kind": resource_type,
        "resource_uid": _text(resource.get("uid")),
        "source_snapshot_id": _text(resource.get("snapshot_id")),
        "observed_at": _text(summary.get("metrics_observed_at")),
        "measurement_window": _text(summary.get("metrics_window")),
        "cpu_mcores": _non_negative_number(summary.get("cpu_mcores")),
        "memory_mib": _non_negative_number(summary.get("mem_mib")),
    }
    if resource_type == "pod":
        return _pod_metrics(common, summary)
    return _node_metrics(common, summary)


def _pod_metrics(common: JsonObject, summary: Mapping[str, Any]) -> JsonObject:
    values: JsonObject = {
        **common,
        "cpu_request_mcores": _positive_number(summary.get("cpu_request_mcores")),
        "cpu_limit_mcores": _positive_number(summary.get("cpu_limit_mcores")),
        "memory_request_mib": _positive_number(summary.get("mem_request_mib")),
        "memory_limit_mib": _positive_number(summary.get("mem_limit_mib")),
    }
    reasons = _common_reasons(values)
    for field, reason in (
        ("cpu_mcores", "pod_cpu_usage_unavailable"),
        ("memory_mib", "pod_memory_usage_unavailable"),
        ("cpu_request_mcores", "pod_cpu_request_unavailable"),
        ("cpu_limit_mcores", "pod_cpu_limit_unavailable"),
        ("memory_request_mib", "pod_memory_request_unavailable"),
        ("memory_limit_mib", "pod_memory_limit_unavailable"),
    ):
        if values[field] is None:
            reasons.add(reason)
    return {
        **values,
        "completeness": _completeness(values, reasons),
        "reason_codes": sorted(reasons),
    }


def _node_metrics(common: JsonObject, summary: Mapping[str, Any]) -> JsonObject:
    allocatable_value = summary.get("allocatable")
    allocatable = allocatable_value if isinstance(allocatable_value, Mapping) else {}
    values: JsonObject = {
        **common,
        "cpu_allocatable_mcores": cpu_millicores(allocatable.get("cpu")),
        "memory_allocatable_mib": memory_mebibytes(allocatable.get("memory")),
        "pod_count": _non_negative_integer(summary.get("pod_count")),
        "pod_allocatable": _non_negative_integer(allocatable.get("pods")),
    }
    reasons = _common_reasons(values)
    for field, reason in (
        ("cpu_mcores", "node_cpu_usage_unavailable"),
        ("memory_mib", "node_memory_usage_unavailable"),
        ("cpu_allocatable_mcores", "node_cpu_allocatable_unavailable"),
        ("memory_allocatable_mib", "node_memory_allocatable_unavailable"),
        ("pod_count", "node_pod_count_unavailable"),
        ("pod_allocatable", "node_pod_allocatable_unavailable"),
    ):
        if values[field] is None:
            reasons.add(reason)
    return {
        **values,
        "completeness": _completeness(values, reasons),
        "reason_codes": sorted(reasons),
    }


def _common_reasons(values: Mapping[str, Any]) -> set[str]:
    reasons: set[str] = set()
    if values.get("resource_uid") is None:
        reasons.add("resource_uid_unavailable")
    if values.get("source_snapshot_id") is None:
        reasons.add("source_snapshot_unavailable")
    if values.get("observed_at") is None:
        reasons.add("metrics_observed_at_unavailable")
    if values.get("measurement_window") is None:
        reasons.add("metrics_window_unavailable")
    return reasons


def _completeness(values: Mapping[str, Any], reasons: set[str]) -> str:
    if values.get("cpu_mcores") is None and values.get("memory_mib") is None:
        return "unavailable"
    return "exact" if not reasons else "partial"


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _non_negative_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _positive_number(value: Any) -> float | None:
    parsed = _non_negative_number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _non_negative_integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None
