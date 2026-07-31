from __future__ import annotations

import math
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

RATIO_WARNING_THRESHOLD = 0.8
RATIO_CRITICAL_THRESHOLD = 0.9

METRIC_KIND_CPU_THROTTLING = "cpu_throttling"
METRIC_KIND_CPU_USAGE = "cpu_usage"
METRIC_KIND_CPU_USAGE_RATIO = "cpu_usage_ratio"
METRIC_KIND_DEPLOYMENT_REPLICA_COUNT = "deployment_replica_count"
METRIC_KIND_FILESYSTEM_USAGE_RATIO = "filesystem_usage_ratio"
METRIC_KIND_GENERIC = "generic"
METRIC_KIND_MEMORY_USAGE = "memory_usage"
METRIC_KIND_MEMORY_USAGE_RATIO = "memory_usage_ratio"
METRIC_KIND_POD_COUNT = "pod_count"
METRIC_KIND_POD_NOT_READY_COUNT = "pod_not_ready_count"
METRIC_KIND_RESTART_COUNT_OR_RATE = "restart_count_or_rate"
METRIC_KIND_SCRAPE_ERROR = "scrape_error"
METRIC_KIND_SCRAPE_HEALTH = "scrape_health"

THRESHOLD_TYPE_NONE = "none"
THRESHOLD_TYPE_POSITIVE = "positive"
THRESHOLD_TYPE_RATIO_HIGH = "ratio_high"
THRESHOLD_TYPE_UP_HEALTH = "up_health"

SIGNAL_BY_KIND = {
    METRIC_KIND_CPU_THROTTLING: "cpu_throttling",
    METRIC_KIND_CPU_USAGE_RATIO: "cpu_pressure",
    METRIC_KIND_FILESYSTEM_USAGE_RATIO: "filesystem_pressure",
    METRIC_KIND_MEMORY_USAGE_RATIO: "memory_pressure",
    METRIC_KIND_POD_NOT_READY_COUNT: "not_ready_pods",
    METRIC_KIND_RESTART_COUNT_OR_RATE: "restart_increase",
    METRIC_KIND_SCRAPE_ERROR: "collector_scrape_error",
    METRIC_KIND_SCRAPE_HEALTH: "scrape_target_down",
}


def build_metric_analysis(metric_name: str, promql: str, normalized: JsonObject) -> JsonObject:
    """Build small RCA-friendly fields from normalized Prometheus data."""
    profile = metric_profile(metric_name, promql)
    points = normalized_points(normalized)
    analysis: JsonObject = {
        "metric_kind": profile["kind"],
        "unit": profile["unit"],
        "signals": [],
    }

    result_type = normalized.get("result_type")
    if result_type == "vector":
        analysis["sample_count"] = len(normalized.get("samples") or [])
    elif result_type == "matrix":
        series = normalized.get("series") or []
        analysis["series_count"] = len(series) if isinstance(series, list) else 0
        analysis["point_count"] = normalized.get("point_count", len(points))

    value_summary = summarize_points(points)
    signals: set[str] = set()
    if value_summary:
        analysis["value_summary"] = value_summary
        threshold = evaluate_threshold(profile, value_summary)
        if threshold:
            analysis["threshold"] = threshold
            if threshold.get("exceeded"):
                signal = SIGNAL_BY_KIND.get(profile["kind"])
                if signal:
                    signals.add(signal)

    if result_type == "matrix":
        baseline = compare_range_baseline(normalized.get("series"))
        if baseline:
            analysis["baseline_comparison"] = baseline
            if baseline.get("increased_series_count", 0) > 0:
                signals.add("increased_from_range_start")

    analysis["signals"] = sorted(signals)
    return {"analysis": analysis}


def metric_profile(metric_name: str, promql: str) -> JsonObject:
    """Classify a metric by name and query text."""
    text = f"{metric_name} {promql}".casefold()
    stripped_promql = promql.strip()

    if "throttl" in text:
        return profile(METRIC_KIND_CPU_THROTTLING, "count_or_ratio", THRESHOLD_TYPE_POSITIVE)
    if "scrape_error" in text:
        return profile(METRIC_KIND_SCRAPE_ERROR, "count", THRESHOLD_TYPE_POSITIVE)
    if metric_name == "scrape_targets_up" or stripped_promql == "up":
        return profile(METRIC_KIND_SCRAPE_HEALTH, "boolean_0_or_1", THRESHOLD_TYPE_UP_HEALTH)
    if "not_ready" in text or "not ready" in text:
        return profile(METRIC_KIND_POD_NOT_READY_COUNT, "count", THRESHOLD_TYPE_POSITIVE)
    if "restart" in text or "restarts" in text:
        return profile(METRIC_KIND_RESTART_COUNT_OR_RATE, "count_or_rate", THRESHOLD_TYPE_POSITIVE)
    if "memory" in text and ("ratio" in text or "/" in stripped_promql):
        return profile(METRIC_KIND_MEMORY_USAGE_RATIO, "ratio", THRESHOLD_TYPE_RATIO_HIGH)
    if "memory" in text and "usage" in text:
        return profile(METRIC_KIND_MEMORY_USAGE, "bytes_or_unknown", THRESHOLD_TYPE_NONE)
    if "cpu" in text and "ratio" in text:
        return profile(METRIC_KIND_CPU_USAGE_RATIO, "ratio", THRESHOLD_TYPE_RATIO_HIGH)
    if "cpu" in text and "usage" in text:
        return profile(METRIC_KIND_CPU_USAGE, "rate_or_cores", THRESHOLD_TYPE_NONE)
    if ("filesystem" in text or "disk" in text) and ("ratio" in text or "/" in stripped_promql):
        return profile(METRIC_KIND_FILESYSTEM_USAGE_RATIO, "ratio", THRESHOLD_TYPE_RATIO_HIGH)
    if "deployment" in text and "replica" in text:
        return profile(METRIC_KIND_DEPLOYMENT_REPLICA_COUNT, "count", THRESHOLD_TYPE_NONE)
    if "pod_count" in text or "pod count" in text:
        return profile(METRIC_KIND_POD_COUNT, "count", THRESHOLD_TYPE_NONE)
    return profile(METRIC_KIND_GENERIC, "unknown", THRESHOLD_TYPE_NONE)


def profile(kind: str, unit: str, threshold_type: str) -> JsonObject:
    """Create a metric profile object."""
    return {
        "kind": kind,
        "unit": unit,
        "threshold_type": threshold_type,
    }


def normalized_points(normalized: JsonObject) -> list[JsonObject]:
    """Flatten vector samples or matrix values into numeric points."""
    result_type = normalized.get("result_type")
    if result_type == "vector":
        return [
            point
            for item in normalized.get("samples") or []
            if isinstance(item, dict)
            for point in [point_from_value(item.get("timestamp"), item.get("value"))]
            if point
        ]
    if result_type == "matrix":
        points: list[JsonObject] = []
        for series in normalized.get("series") or []:
            if not isinstance(series, dict):
                continue
            for item in series.get("values") or []:
                if isinstance(item, dict):
                    point = point_from_value(item.get("timestamp"), item.get("value"))
                    if point:
                        points.append(point)
        return points
    return []


def point_from_value(timestamp: Any, value: Any) -> JsonObject | None:
    """Create one point if the value is a finite number."""
    parsed = finite_number(value)
    if parsed is None:
        return None
    return {
        "timestamp": timestamp,
        "value": parsed,
    }


def finite_number(value: Any) -> float | None:
    """Parse finite numbers and skip NaN or infinity."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def summarize_points(points: list[JsonObject]) -> JsonObject:
    """Summarize numeric points without copying all samples."""
    values = [point["value"] for point in points if isinstance(point.get("value"), (int, float))]
    if not values:
        return {}

    latest = latest_point(points)
    summary: JsonObject = {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
    }
    if latest:
        summary["latest"] = latest["value"]
        summary["latest_timestamp"] = latest.get("timestamp")
    return summary


def latest_point(points: list[JsonObject]) -> JsonObject | None:
    """Return the newest point when timestamps can be compared."""
    if not points:
        return None
    return max(enumerate(points), key=lambda item: timestamp_key(item[1], item[0]))[1]


def timestamp_key(point: JsonObject, index: int) -> tuple[float, int]:
    """Build a stable sort key for timestamps."""
    timestamp = point.get("timestamp")
    try:
        return (float(timestamp), index)
    except (TypeError, ValueError):
        return (float(index), index)


def evaluate_threshold(profile_data: JsonObject, summary: JsonObject) -> JsonObject:
    """Evaluate a conservative threshold for known metric kinds."""
    threshold_type = profile_data.get("threshold_type")
    if threshold_type == THRESHOLD_TYPE_RATIO_HIGH:
        observed = summary["max"]
        level = "critical" if observed >= RATIO_CRITICAL_THRESHOLD else "none"
        if level == "none" and observed >= RATIO_WARNING_THRESHOLD:
            level = "warning"
        return {
            "comparator": "greater_than_or_equal",
            "warning": RATIO_WARNING_THRESHOLD,
            "critical": RATIO_CRITICAL_THRESHOLD,
            "observed_value": observed,
            "level": level,
            "exceeded": level != "none",
        }
    if threshold_type == THRESHOLD_TYPE_POSITIVE:
        observed = summary["max"]
        return {
            "comparator": "greater_than",
            "warning": 0.0,
            "observed_value": observed,
            "level": "warning" if observed > 0 else "none",
            "exceeded": observed > 0,
        }
    if threshold_type == THRESHOLD_TYPE_UP_HEALTH:
        observed = summary["min"]
        return {
            "comparator": "less_than",
            "critical": 1.0,
            "observed_value": observed,
            "level": "critical" if observed < 1.0 else "none",
            "exceeded": observed < 1.0,
        }
    return {}


def compare_range_baseline(series_value: Any) -> JsonObject:
    """Compare each range series with its first point in the same window."""
    if not isinstance(series_value, list):
        return {}

    increased = 0
    decreased = 0
    flat = 0
    max_delta: float | None = None
    max_percent_change: float | None = None
    considered = 0

    for series in series_value:
        if not isinstance(series, dict):
            continue
        values = [
            point["value"]
            for item in series.get("values") or []
            if isinstance(item, dict)
            for point in [point_from_value(item.get("timestamp"), item.get("value"))]
            if point
        ]
        if len(values) < 2:
            continue

        considered += 1
        delta = values[-1] - values[0]
        if delta > 0:
            increased += 1
        elif delta < 0:
            decreased += 1
        else:
            flat += 1

        max_delta = delta if max_delta is None else max(max_delta, delta)
        percent_change = percent_delta(values[0], delta)
        if percent_change is not None:
            max_percent_change = (
                percent_change
                if max_percent_change is None
                else max(max_percent_change, percent_change)
            )

    if considered == 0:
        return {}
    return {
        "basis": "first_point_in_range",
        "series_count": considered,
        "increased_series_count": increased,
        "decreased_series_count": decreased,
        "flat_series_count": flat,
        "max_delta": max_delta,
        "max_percent_change": max_percent_change,
    }


def percent_delta(first: float, delta: float) -> float | None:
    """Return percent change when the first value is not zero."""
    if first == 0:
        return None
    return delta / abs(first)
