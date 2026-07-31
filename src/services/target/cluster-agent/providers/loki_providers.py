from __future__ import annotations

import re
import time

import httpx
from queries import LokiLogQuery
from telemetry_registry import telemetry

from config import (
    DEFAULT_LOKI_BASE_URL,
    LOKI_BASE_URL_ENV,
    LOKI_QUERY_LIMIT,
    LOKI_TIMEOUT_SECONDS,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.security.log_lines import (
    redact_log_line,
    truncate_log_line,
)
from providers.base import TRACER, ConfigReader

MAX_TRACE_IDS = 20
MAX_MATCHED_LOG_ENTRIES = 20

LOG_PATTERN_NAMES = (
    "app_port_bind_failed",
    "permission_denied_startup",
    "missing_env",
    "probe_failed",
    "health_endpoint_error",
    "dependency_timeout",
    "dependency_error",
    "image_pull_error",
    "oom_or_memory",
    "config_error",
)
SEVERITY_NAMES = ("critical", "error", "warn", "info", "debug", "trace", "unknown")
SEVERITY_ALIASES = {
    "fatal": "critical",
    "crit": "critical",
    "critical": "critical",
    "err": "error",
    "error": "error",
    "warning": "warn",
    "warn": "warn",
    "info": "info",
    "debug": "debug",
    "trace": "trace",
}

LOG_PATTERN_MATCHERS = {
    "app_port_bind_failed": (
        re.compile(r"\baddress\s+already\s+in\s+use\b", re.I),
        re.compile(r"\bport\s+already\s+in\s+use\b", re.I),
        re.compile(r"\blisten\s+tcp\b.*\b(?:bind|failed|failure|error)\b", re.I),
        re.compile(r"\bbind:\s*permission\s+denied\b", re.I),
    ),
    "permission_denied_startup": (
        re.compile(r"\bpermission\s+denied\b", re.I),
        re.compile(r"\boperation\s+not\s+permitted\b", re.I),
        re.compile(r"\bread-only\s+file\s+system\b", re.I),
    ),
    "missing_env": (
        re.compile(r"\bmissing\s+(?:required\s+)?env(?:ironment)?\b", re.I),
        re.compile(r"\benvironment\s+variable\b.*\b(?:missing|required|not\s+set)\b", re.I),
        re.compile(r"\b(?:env|environment)\b.*\bnot\s+set\b", re.I),
    ),
    "probe_failed": (
        re.compile(
            r"\b(?:readiness|liveness|startup)\s+probe\s+(?:failed|failure)\b",
            re.I,
        ),
        re.compile(r"\bprobe\s+(?:failed|failure)\b", re.I),
        re.compile(r"\bunhealthy\b", re.I),
    ),
    "health_endpoint_error": (
        re.compile(
            r"\bhealth(?:check|\s+check|\s+endpoint)?\b.*"
            r"\b(?:failed|failure|error|unhealthy)\b",
            re.I,
        ),
        re.compile(
            r"\b/(?:health|ready|live|startup)\b.*"
            r"\b(?:5\d\d|failed|failure|error|timeout)\b",
            re.I,
        ),
    ),
    "dependency_timeout": (
        re.compile(
            r"\b(?:timeout|timed out|deadline exceeded|context deadline exceeded|etimedout)\b",
            re.I,
        ),
        re.compile(r"\bconnection\s+timed\s+out\b", re.I),
    ),
    "dependency_error": (
        re.compile(
            r"\b(?:connection refused|connection reset|no such host|dns lookup failed)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:upstream|downstream|dependency)\b.*"
            r"\b(?:failed|failure|error|unavailable)\b",
            re.I,
        ),
    ),
    "image_pull_error": (
        re.compile(r"\b(?:ImagePullBackOff|ErrImagePull)\b", re.I),
        re.compile(
            r"\b(?:failed to pull image|pull access denied|manifest unknown|"
            r"repository does not exist)\b",
            re.I,
        ),
        re.compile(r"\bunauthorized\b.*\b(?:image|registry|pull)\b", re.I),
    ),
    "oom_or_memory": (
        re.compile(r"\b(?:OOMKilled|out of memory|heap out of memory)\b", re.I),
        re.compile(r"\bmemory\b.*\b(?:limit|exceeded|pressure)\b", re.I),
    ),
    "config_error": (
        re.compile(
            r"\b(?:configmap|secret|env|environment|volume|mount)\b.*"
            r"\b(?:not found|missing|failed|failure|error|invalid|denied)\b",
            re.I,
        ),
        re.compile(r"\b(?:key|file)\b.*\bnot found\b", re.I),
    ),
}

STRUCTURED_SEVERITY_RE = re.compile(
    r"(?i)(?:^|[\s,{])(?:level|severity|lvl|loglevel)[\"']?\s*[:=]\s*[\"']?"
    r"(critical|fatal|crit|error|err|warn|warning|info|debug|trace)\b"
)
TOKEN_SEVERITY_RE = re.compile(
    r"(?i)(?:^|[\s\[\(])"
    r"(critical|fatal|crit|error|err|warn|warning|info|debug|trace)"
    r"(?:$|[\s\]\):,\-])"
)
TRACE_ID_PATTERNS = (
    re.compile(
        r"(?i)\btraceparent[\"']?\s*[:=]\s*[\"']?"
        r"00-([a-f0-9]{32})-[a-f0-9]{16}-[a-f0-9]{2}\b"
    ),
    re.compile(r"(?i)\btrace[_-]?id[\"']?\s*[:=]\s*[\"']?([a-f0-9]{32})\b"),
    re.compile(r"(?i)\bx-b3-traceid[\"']?\s*[:=]\s*[\"']?([a-f0-9]{32})\b"),
)


def empty_pattern_counts() -> dict[str, int]:
    """Create stable pattern count keys for one Loki result."""
    return {name: 0 for name in LOG_PATTERN_NAMES}


def empty_severity_counts() -> dict[str, int]:
    """Create stable severity count keys for one Loki result."""
    return {name: 0 for name in SEVERITY_NAMES}


def extract_severity(line: str) -> str:
    """Find a normalized severity value in one log line."""
    match = STRUCTURED_SEVERITY_RE.search(line) or TOKEN_SEVERITY_RE.search(line)
    if match is None:
        return "unknown"
    return SEVERITY_ALIASES.get(match.group(1).lower(), "unknown")


def collect_trace_ids(line: str, trace_ids: list[str], seen: set[str]) -> None:
    """Add safe trace IDs from one log line."""
    for trace_id in trace_ids_from_line(line):
        if trace_id not in seen and len(trace_ids) < MAX_TRACE_IDS:
            seen.add(trace_id)
            trace_ids.append(trace_id)


def trace_ids_from_line(line: str) -> list[str]:
    """Return safe trace IDs found in one log line."""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in TRACE_ID_PATTERNS:
        for match in pattern.finditer(line):
            trace_id = match.group(1).lower()
            if trace_id not in seen:
                seen.add(trace_id)
                found.append(trace_id)
    return found


def matched_log_patterns(line: str) -> list[str]:
    """Return RCA diagnostic pattern names matched by one log line."""
    return [
        name
        for name, matchers in LOG_PATTERN_MATCHERS.items()
        if any(matcher.search(line) for matcher in matchers)
    ]


def update_log_summaries(
    line: str,
    pattern_counts: dict[str, int],
    severity_counts: dict[str, int],
    trace_ids: list[str],
    seen_trace_ids: set[str],
    *,
    severity: str | None = None,
    patterns: list[str] | None = None,
) -> None:
    """Update structured log summaries from one redacted line."""
    for name in matched_log_patterns(line) if patterns is None else patterns:
        pattern_counts[name] += 1

    severity_counts[severity or extract_severity(line)] += 1
    collect_trace_ids(line, trace_ids, seen_trace_ids)


def stream_label(stream: JsonObject, keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty label value from a Loki stream."""
    for key in keys:
        value = stream.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def matched_entry(
    *,
    timestamp: object,
    stream: JsonObject,
    message: str,
    severity: str,
    patterns: list[str],
    line_truncated: bool,
) -> JsonObject:
    """Build one RCA-friendly matched log entry."""
    entry: JsonObject = {
        "timestamp": timestamp,
        "namespace": stream_label(stream, ("k8s_namespace_name", "namespace")),
        "pod": stream_label(stream, ("k8s_pod_name", "pod", "pod_name", "kubernetes_pod_name")),
        "container": stream_label(
            stream,
            ("k8s_container_name", "container", "container_name", "kubernetes_container_name"),
        ),
        "severity": severity,
        "message": message,
        "matched_patterns": patterns,
        "trace_id": None,
        "line_truncated": line_truncated,
    }
    trace_ids = trace_ids_from_line(message)
    if trace_ids:
        entry["trace_id"] = trace_ids[0]
    return entry


@telemetry.source(
    source="loki",
    evidence_key="logs",
    query_type=LokiLogQuery,
    empty_payload=list,  # log payload's shape is list
    range_query_type=LokiLogQuery,
)
class LokiLogsProvider:
    """Collect log data from Loki.
    It builds the logs evidence bucket.
    """

    span_name = "loki.collect"
    query_count_attribute = "loki.query_count"
    result_count_attribute = "loki.result_count"
    timeout_seconds = LOKI_TIMEOUT_SECONDS
    failure_message = "loki log collection failed"
    queries: tuple[LokiLogQuery, ...] = ()

    def __init__(self, base_url: str) -> None:
        """Store the Loki base URL without a trailing slash."""
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_config(cls, read_config: ConfigReader) -> LokiLogsProvider:
        """Create the provider from agent config values."""
        return cls(read_config(LOKI_BASE_URL_ENV, DEFAULT_LOKI_BASE_URL))

    async def query(
        self,
        client: httpx.AsyncClient,
        telemetry_query: LokiLogQuery,
    ) -> JsonObject:
        """Run one Loki query and return the raw API result."""
        with TRACER.start_as_current_span("loki.query_range") as span:
            span.attr("loki.query", telemetry_query.logql)
            params: dict[str, str | int] = {
                "query": telemetry_query.logql,
                "limit": LOKI_QUERY_LIMIT,
            }
            if telemetry_query.range_seconds is not None:
                end_ns = time.time_ns()
                params.update(
                    {
                        "start": end_ns - telemetry_query.range_seconds * 1_000_000_000,
                        "end": end_ns,
                        "direction": "backward",
                    }
                )
            response = await client.get(
                f"{self.base_url}/loki/api/v1/query_range",
                params=params,
            )
            span.http_status(response.status_code)
            response.raise_for_status()
            return response.json()

    def empty_results(self) -> list[JsonObject]:
        """Create an empty logs evidence bucket."""
        return []

    def append_result(
        self,
        results: list[JsonObject],
        telemetry_query: LokiLogQuery,
        payload: JsonObject,
    ) -> None:
        """Normalize one Loki result and add it to the bucket."""
        result = {
            "source": self.source,
            "query_name": telemetry_query.query_name,
            "query": telemetry_query.logql,
            **self.normalize_payload(payload),
        }
        if telemetry_query.range_seconds is not None:
            result["range_seconds"] = telemetry_query.range_seconds
        results.append(result)

    def build_response(self, results: list[JsonObject]) -> list[JsonObject]:
        """Return the finished logs evidence bucket."""
        return results

    def normalize_payload(self, payload: JsonObject) -> JsonObject:
        """Turn a Loki response into stream and line summaries."""
        data = payload.get("data", {})
        result_type = data.get("resultType")
        result = data.get("result", [])
        streams = []
        pattern_counts = empty_pattern_counts()
        severity_counts = empty_severity_counts()
        trace_ids: list[str] = []
        seen_trace_ids: set[str] = set()
        matched_entries: list[JsonObject] = []
        matched_entry_count = 0
        redacted_line_count = 0
        truncated_line_count = 0

        for item in result:
            stream = item.get("stream", {})
            stream = stream if isinstance(stream, dict) else {}
            stream_pattern_counts = empty_pattern_counts()
            stream_severity_counts = empty_severity_counts()
            stream_trace_ids: list[str] = []
            stream_seen_trace_ids: set[str] = set()
            values = []
            for raw_entry in item.get("values", []):
                raw_line = raw_entry[1] if len(raw_entry) >= 2 else None
                line = raw_line
                line_truncated = False
                original_line_length = None
                if isinstance(raw_line, str):
                    line = redact_log_line(raw_line)
                    if line != raw_line:
                        redacted_line_count += 1
                    severity = extract_severity(line)
                    patterns = matched_log_patterns(line)
                    update_log_summaries(
                        line,
                        pattern_counts,
                        severity_counts,
                        trace_ids,
                        seen_trace_ids,
                        severity=severity,
                        patterns=patterns,
                    )
                    update_log_summaries(
                        line,
                        stream_pattern_counts,
                        stream_severity_counts,
                        stream_trace_ids,
                        stream_seen_trace_ids,
                        severity=severity,
                        patterns=patterns,
                    )
                    original_line_length = len(line)
                    line, line_truncated = truncate_log_line(line)
                    if line_truncated:
                        truncated_line_count += 1
                    if patterns:
                        matched_entry_count += 1
                        if len(matched_entries) < MAX_MATCHED_LOG_ENTRIES:
                            matched_entries.append(
                                matched_entry(
                                    timestamp=raw_entry[0] if len(raw_entry) >= 1 else None,
                                    stream=stream,
                                    message=line,
                                    severity=severity,
                                    patterns=patterns,
                                    line_truncated=line_truncated,
                                )
                            )

                value = {
                    "timestamp": raw_entry[0] if len(raw_entry) >= 1 else None,
                    "line": line,
                }
                if line_truncated:
                    value["line_truncated"] = True
                    value["original_line_length"] = original_line_length
                values.append(value)

            streams.append(
                {
                    "stream": stream,
                    "values": values,
                    "line_count": len(values),
                    "pattern_counts": stream_pattern_counts,
                    "severity_counts": stream_severity_counts,
                    "trace_ids": stream_trace_ids,
                }
            )

        return {
            "result_type": result_type,
            "streams": streams,
            "line_count": sum(len(stream["values"]) for stream in streams),
            "pattern_counts": pattern_counts,
            "severity_counts": severity_counts,
            "trace_ids": trace_ids,
            "matched_entries": matched_entries,
            "collection_limit": {
                "matched_entries": {
                    "max_items": MAX_MATCHED_LOG_ENTRIES,
                    "original_count": matched_entry_count,
                    "returned_count": len(matched_entries),
                    "truncated": matched_entry_count > len(matched_entries),
                }
            },
            "redaction_summary": {
                "applied": True,
                "redacted_line_count": redacted_line_count,
                "truncated_line_count": truncated_line_count,
            },
        }
