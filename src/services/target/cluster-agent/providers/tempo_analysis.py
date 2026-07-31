from __future__ import annotations

import math
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

MAX_ANALYSIS_TRACES = 20
MAX_SPAN_SUMMARIES_PER_TRACE = 8
MAX_SUMMARY_STRING_LENGTH = 256

TRACE_ID_KEYS = ("trace_id", "traceID", "traceId")
SPAN_ID_KEYS = ("span_id", "spanID", "spanId")
SERVICE_KEYS = (
    "service",
    "service.name",
    "service_name",
    "serviceName",
    "rootServiceName",
    "resource.service.name",
)
OPERATION_KEYS = (
    "operation",
    "operation_name",
    "operationName",
    "name",
    "spanName",
    "rootTraceName",
)
STATUS_KEYS = ("status", "status_code", "statusCode", "statusCodeString")
ERROR_KEYS = ("error", "errored", "has_error", "hasError")
DURATION_FIELDS = (
    ("duration_ms", 1.0),
    ("durationMs", 1.0),
    ("duration_millis", 1.0),
    ("durationNanos", 0.000001),
    ("durationNano", 0.000001),
    ("duration_ns", 0.000001),
    ("durationSeconds", 1000.0),
    ("duration_seconds", 1000.0),
    ("duration_s", 1000.0),
)

STATUS_NAMES = ("error", "ok", "unset", "unknown")
ERROR_STATUS_VALUES = {
    "2",
    "error",
    "err",
    "failed",
    "failure",
    "status_code_error",
    "statuscodeerror",
}
OK_STATUS_VALUES = {"1", "ok", "success", "status_code_ok", "statuscodeok"}
UNSET_STATUS_VALUES = {"0", "unset", "status_code_unset", "statuscodeunset"}
DEPENDENCY_SPAN_KINDS = {"client", "producer", "consumer"}
DEPENDENCY_ATTRIBUTE_KEYS = (
    "db.system",
    "rpc.system",
    "messaging.system",
    "peer.service",
    "server.address",
    "net.peer.name",
)


def build_trace_analysis(normalized: JsonObject) -> JsonObject:
    """Build small RCA-friendly fields from normalized Tempo data."""
    traces = normalized.get("traces")
    if not isinstance(traces, list):
        traces = []

    trace_summaries = [
        summary
        for item in traces[:MAX_ANALYSIS_TRACES]
        if isinstance(item, dict)
        for summary in [trace_summary(item)]
        if summary
    ]

    analysis: JsonObject = {
        "trace_summaries": trace_summaries,
        "trace_ids": unique_values(summary.get("trace_id") for summary in trace_summaries),
        "services": unique_values(summary.get("service") for summary in trace_summaries),
        "operations": unique_values(summary.get("operation") for summary in trace_summaries),
        "status_counts": count_statuses(trace_summaries),
        "error_count": sum(1 for summary in trace_summaries if summary.get("error") is True),
        "dependency_count": sum(
            1 for summary in trace_summaries if summary.get("is_dependency") is True
        ),
    }

    span_summaries = [
        span
        for summary in trace_summaries
        for span in summary.get("span_summaries", [])
        if isinstance(span, dict)
    ]
    if span_summaries:
        analysis["span_count"] = len(span_summaries)
        analysis["error_span_count"] = sum(
            1 for summary in span_summaries if summary.get("error") is True
        )
        analysis["dependency_span_count"] = sum(
            1 for summary in span_summaries if summary.get("is_dependency") is True
        )

    duration_summary = summarize_durations(trace_summaries)
    if duration_summary:
        analysis["duration_ms"] = duration_summary

    return {"analysis": analysis}


def trace_summary(trace: JsonObject) -> JsonObject:
    """Create one safe trace summary from a Tempo trace object."""
    spans = span_summaries(trace)
    status = normalized_status(first_present(trace, STATUS_KEYS))
    error = (
        is_error_status(status)
        or has_error_flag(trace)
        or any(span.get("error") is True for span in spans)
    )
    is_dependency = is_dependency_trace(trace) or any(
        span.get("is_dependency") is True for span in spans
    )

    summary = compact_fields(
        {
            "trace_id": safe_string(first_present(trace, TRACE_ID_KEYS)),
            "service": safe_string(first_present(trace, SERVICE_KEYS)),
            "operation": safe_string(first_present(trace, OPERATION_KEYS)),
            "status": status,
            "duration_ms": duration_ms(trace),
            "error": error,
            "is_dependency": is_dependency,
            "span_summaries": spans,
        }
    )
    if "status" not in summary:
        summary["status"] = "unknown"
    return summary


def span_summaries(trace: JsonObject) -> list[JsonObject]:
    """Create small span summaries without copying raw attributes."""
    parent_trace_id = safe_string(first_present(trace, TRACE_ID_KEYS))
    summaries: list[JsonObject] = []
    for span in trace_spans(trace):
        if len(summaries) >= MAX_SPAN_SUMMARIES_PER_TRACE:
            break
        if not isinstance(span, dict):
            continue
        summary = span_summary(span, parent_trace_id)
        if summary:
            summaries.append(summary)
    return summaries


def span_summary(span: JsonObject, parent_trace_id: str | None) -> JsonObject:
    """Create one safe span summary from a Tempo span object."""
    attributes = extract_attributes(span)
    status = normalized_status(first_present(span, STATUS_KEYS))
    service = first_present(span, SERVICE_KEYS) or first_present(attributes, SERVICE_KEYS)
    operation = first_present(span, OPERATION_KEYS) or first_present(attributes, OPERATION_KEYS)
    error = is_error_status(status) or has_error_flag(span) or has_error_flag(attributes)
    is_dependency = is_dependency_span(span, attributes)

    summary = compact_fields(
        {
            "trace_id": safe_string(first_present(span, TRACE_ID_KEYS) or parent_trace_id),
            "span_id": safe_string(first_present(span, SPAN_ID_KEYS)),
            "service": safe_string(service),
            "operation": safe_string(operation),
            "status": status,
            "duration_ms": duration_ms(span),
            "error": error,
            "is_dependency": is_dependency,
        }
    )
    if "status" not in summary:
        summary["status"] = "unknown"
    return summary


def trace_spans(trace: JsonObject) -> list[JsonObject]:
    """Find span lists in common Tempo search response shapes."""
    spans: list[JsonObject] = []
    direct_spans = trace.get("spans")
    if isinstance(direct_spans, list):
        spans.extend(item for item in direct_spans if isinstance(item, dict))

    for key in ("spanSet", "span_set"):
        span_set = trace.get(key)
        if isinstance(span_set, dict):
            nested = span_set.get("spans")
            if isinstance(nested, list):
                spans.extend(item for item in nested if isinstance(item, dict))

    span_sets = trace.get("spanSets")
    if isinstance(span_sets, list):
        for span_set in span_sets:
            if not isinstance(span_set, dict):
                continue
            nested = span_set.get("spans")
            if isinstance(nested, list):
                spans.extend(item for item in nested if isinstance(item, dict))

    return spans


def extract_attributes(item: JsonObject) -> JsonObject:
    """Read attributes from Tempo dict or key/value list formats."""
    attributes: JsonObject = {}
    for key in ("attributes", "resource", "resourceAttributes"):
        raw = item.get(key)
        if isinstance(raw, dict):
            if key == "resource" and isinstance(raw.get("attributes"), (dict, list)):
                attributes.update(attribute_mapping(raw["attributes"]))
            else:
                attributes.update(attribute_mapping(raw))
        elif isinstance(raw, list):
            attributes.update(attribute_mapping(raw))
    return attributes


def attribute_mapping(raw: Any) -> JsonObject:
    """Convert raw attribute shapes into a simple dict."""
    if isinstance(raw, dict):
        return {str(key): attribute_value(value) for key, value in raw.items()}
    if isinstance(raw, list):
        values: JsonObject = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key in (None, ""):
                continue
            values[str(key)] = attribute_value(item.get("value"))
        return values
    return {}


def attribute_value(value: Any) -> Any:
    """Unwrap OpenTelemetry attribute value objects."""
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            return value[key]
    return value


def first_present(mapping: JsonObject, keys: tuple[str, ...]) -> Any:
    """Return the first non-empty value for known key aliases."""
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def safe_string(value: Any) -> str | None:
    """Return a bounded string value."""
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) <= MAX_SUMMARY_STRING_LENGTH:
        return text
    return f"{text[:MAX_SUMMARY_STRING_LENGTH]}..."


def duration_ms(item: JsonObject) -> float | None:
    """Read common duration fields and return milliseconds."""
    for key, multiplier in DURATION_FIELDS:
        if key not in item:
            continue
        value = finite_number(item.get(key))
        if value is not None:
            return value * multiplier
    return None


def finite_number(value: Any) -> float | None:
    """Parse a number and skip invalid values."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def normalized_status(value: Any) -> str:
    """Normalize common OpenTelemetry status values."""
    if isinstance(value, dict):
        value = first_present(value, STATUS_KEYS + ("code",))
    if value in (None, ""):
        return "unknown"

    text = str(value).casefold()
    cleaned = text.replace(".", "_").replace("-", "_").replace(" ", "_")
    if cleaned in ERROR_STATUS_VALUES or "error" in cleaned:
        return "error"
    if cleaned in OK_STATUS_VALUES:
        return "ok"
    if cleaned in UNSET_STATUS_VALUES:
        return "unset"
    return "unknown"


def is_error_status(status: str) -> bool:
    """Return true when the normalized status is error."""
    return status == "error"


def has_error_flag(item: JsonObject) -> bool:
    """Read common boolean error fields."""
    for key in ERROR_KEYS:
        value = item.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.casefold() in {"true", "yes", "1"}:
            return True
    return False


def is_dependency_trace(trace: JsonObject) -> bool:
    """Detect dependency traces from explicit root span kind only."""
    span_kind = safe_string(first_present(trace, ("kind", "spanKind", "span.kind")))
    return bool(normalized_span_kind(span_kind) in DEPENDENCY_SPAN_KINDS)


def is_dependency_span(span: JsonObject, attributes: JsonObject) -> bool:
    """Detect dependency spans from span kind or known dependency attribute keys."""
    span_kind = safe_string(
        first_present(span, ("kind", "spanKind", "span.kind"))
        or first_present(attributes, ("span.kind",))
    )
    if normalized_span_kind(span_kind) in DEPENDENCY_SPAN_KINDS:
        return True
    return any(key in attributes for key in DEPENDENCY_ATTRIBUTE_KEYS)


def normalized_span_kind(value: str | None) -> str:
    """Normalize common OpenTelemetry span kind values."""
    if not value:
        return ""
    text = value.casefold().replace(".", "_").replace("-", "_")
    return text.removeprefix("span_kind_").removeprefix("kind_")


def compact_fields(data: JsonObject) -> JsonObject:
    """Drop empty fields but keep false booleans."""
    return {
        key: value
        for key, value in data.items()
        if value not in (None, "", [], {}) or isinstance(value, bool)
    }


def unique_values(values: Any) -> list[str]:
    """Return unique string values in first-seen order."""
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def count_statuses(trace_summaries: list[JsonObject]) -> dict[str, int]:
    """Count trace status names with stable keys."""
    counts = {name: 0 for name in STATUS_NAMES}
    for summary in trace_summaries:
        status = summary.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
        else:
            counts["unknown"] += 1
    return counts


def summarize_durations(trace_summaries: list[JsonObject]) -> JsonObject:
    """Summarize trace duration values in milliseconds."""
    values = [
        value
        for summary in trace_summaries
        for value in [summary.get("duration_ms")]
        if isinstance(value, (int, float))
    ]
    if not values:
        return {}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
    }
