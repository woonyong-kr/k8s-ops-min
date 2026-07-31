from __future__ import annotations

import time

import httpx
from queries import OpenTelemetrySpanQuery
from telemetry_registry import telemetry

from config import (
    DEFAULT_TEMPO_BASE_URL,
    TEMPO_BASE_URL_ENV,
    TEMPO_QUERY_LIMIT,
    TEMPO_TIMEOUT_SECONDS,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.evidence_policy import (
    TEMPO_RECENT_TRACE_QUERY_NAME,
    TEMPO_RECENT_TRACE_RANGE_SECONDS,
)
from providers.base import TRACER, ConfigReader
from providers.collection_limits import (
    attach_collection_limits,
    limit_payload_size,
    payload_size_bytes,
)
from providers.tempo_analysis import build_trace_analysis

TEMPO_TRACES_KEY = "traces"
MAX_TEMPO_TRACE_STRING_LENGTH = 1024
MAX_TEMPO_TRACE_NESTED_LIST_ITEMS = 20
MAX_TEMPO_TRACE_ITEM_BYTES = 64_000
TRUNCATED_TEMPO_VALUE_SUFFIX = " [TRUNCATED]"


@telemetry.source(
    source="tempo",
    evidence_key="traces",
    query_type=OpenTelemetrySpanQuery,
    range_query_type=OpenTelemetrySpanQuery,
)
class TempoTracesProvider:
    """Collect trace data from Tempo.
    It builds the traces evidence bucket.
    """

    span_name = "tempo.collect"
    query_count_attribute = "tempo.query_count"
    result_count_attribute = "tempo.result_count"
    timeout_seconds = TEMPO_TIMEOUT_SECONDS
    failure_message = "tempo trace collection failed"
    queries: tuple[OpenTelemetrySpanQuery, ...] = ()

    def __init__(self, base_url: str) -> None:
        """Store the Tempo base URL without a trailing slash."""
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_config(cls, read_config: ConfigReader) -> TempoTracesProvider:
        """Create the provider from agent config values."""
        return cls(read_config(TEMPO_BASE_URL_ENV, DEFAULT_TEMPO_BASE_URL))

    async def query(
        self,
        client: httpx.AsyncClient,
        telemetry_query: OpenTelemetrySpanQuery,
    ) -> JsonObject:
        """Run one Tempo search query and return the raw result."""
        with TRACER.start_as_current_span("tempo.search") as span:
            span.attr("tempo.traceql", telemetry_query.traceql)
            range_seconds = tempo_query_range_seconds(telemetry_query)
            if range_seconds is not None:
                span.attr("tempo.range_seconds", range_seconds)
            response = await client.get(
                f"{self.base_url}/api/search",
                params=tempo_search_params(telemetry_query),
            )
            span.http_status(response.status_code)
            response.raise_for_status()
            return response.json()

    def empty_results(self) -> JsonObject:
        """Create an empty traces evidence bucket."""
        return {}

    def append_result(
        self,
        results: JsonObject,
        telemetry_query: OpenTelemetrySpanQuery,
        payload: JsonObject,
    ) -> None:
        """Normalize one Tempo result and save it by query name."""
        normalized = self.normalize_payload(payload)
        analysis = build_trace_analysis(normalized)
        result = {
            "query": telemetry_query.traceql,
            **normalized,
            **analysis,
        }
        limit_tempo_payload(result)
        results[telemetry_query.query_name] = result

    def build_response(self, results: JsonObject) -> JsonObject:
        """Return the finished traces evidence bucket."""
        return {
            "source": self.source,
            "results": results,
        }

    def normalize_payload(self, payload: JsonObject) -> JsonObject:
        """Turn a Tempo response into trace list and count data."""
        traces = payload.get("traces", [])
        if not isinstance(traces, list):
            traces = []

        normalized = {
            TEMPO_TRACES_KEY: [compact_tempo_trace(trace) for trace in traces],
            "trace_count": len(traces),
        }
        return normalized


def tempo_search_params(
    telemetry_query: OpenTelemetrySpanQuery,
    *,
    now_seconds: float | None = None,
) -> dict[str, str | int]:
    """Build a bounded Tempo search request.

    Tempo 2.9 searches only ingesters when ``start``/``end`` are omitted.
    Supplying the policy range makes the RCA evidence horizon deterministic and
    permits bounded recent-block lookup without expanding to the full retention
    window.
    """
    params: dict[str, str | int] = {
        "q": telemetry_query.traceql,
        "limit": TEMPO_QUERY_LIMIT,
    }
    range_seconds = tempo_query_range_seconds(telemetry_query)
    if range_seconds is None:
        return params

    end = int(time.time() if now_seconds is None else now_seconds)
    params.update(
        {
            "start": end - range_seconds,
            "end": end,
        }
    )
    return params


def tempo_query_range_seconds(telemetry_query: OpenTelemetrySpanQuery) -> int | None:
    """Resolve a policy range or the canonical self-upgrade compatibility bound."""

    if telemetry_query.range_seconds is not None:
        return telemetry_query.range_seconds
    if telemetry_query.query_name == TEMPO_RECENT_TRACE_QUERY_NAME:
        return TEMPO_RECENT_TRACE_RANGE_SECONDS
    return None


def limit_tempo_payload(payload: JsonObject) -> JsonObject:
    """Limit Tempo trace lists when a result is still too large."""
    limits: JsonObject = {}
    limit_payload_size(payload, list_keys=(TEMPO_TRACES_KEY,), limits=limits)
    attach_collection_limits(payload, limits)
    return payload


def compact_tempo_trace(trace: object) -> object:
    """Keep one Tempo trace object useful but bounded."""
    compacted = compact_tempo_value(trace)
    if payload_size_bytes({"trace": compacted}) <= MAX_TEMPO_TRACE_ITEM_BYTES:
        return compacted
    if isinstance(trace, dict):
        return compact_tempo_trace_summary(trace)
    return {
        "trace_truncated": True,
        "original_trace_bytes": payload_size_bytes({"trace": trace}),
    }


def compact_tempo_value(value: object) -> object:
    """Recursively trim long Tempo strings and nested lists."""
    if isinstance(value, str):
        return truncate_tempo_string(value)
    if isinstance(value, list):
        return [compact_tempo_value(item) for item in value[:MAX_TEMPO_TRACE_NESTED_LIST_ITEMS]]
    if isinstance(value, dict):
        compacted: JsonObject = {}
        for key, item in value.items():
            compacted[str(key)] = compact_tempo_value(item)
            if isinstance(item, list) and len(item) > MAX_TEMPO_TRACE_NESTED_LIST_ITEMS:
                compacted[f"{key}_count"] = len(item)
                compacted[f"{key}_truncated"] = True
        return compacted
    return value


def truncate_tempo_string(value: str) -> str:
    """Trim one long Tempo string field."""
    if len(value) <= MAX_TEMPO_TRACE_STRING_LENGTH:
        return value
    keep_length = max(0, MAX_TEMPO_TRACE_STRING_LENGTH - len(TRUNCATED_TEMPO_VALUE_SUFFIX))
    return f"{value[:keep_length]}{TRUNCATED_TEMPO_VALUE_SUFFIX}"


def compact_tempo_trace_summary(trace: JsonObject) -> JsonObject:
    """Fallback to the same safe fields used by trace analysis."""
    analysis = build_trace_analysis({TEMPO_TRACES_KEY: [trace]}).get("analysis", {})
    summaries = analysis.get("trace_summaries")
    summary = dict(summaries[0]) if isinstance(summaries, list) and summaries else {}
    summary["trace_truncated"] = True
    summary["original_trace_bytes"] = payload_size_bytes({"trace": trace})
    return summary
