from __future__ import annotations

import ipaddress
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from queries import PrometheusInstantQuery, PrometheusRangeQuery
from telemetry_registry import telemetry

from config import PROMETHEUS_TIMEOUT_SECONDS
from packages.contracts.event_bus.interfaces import JsonObject
from providers.base import TRACER
from providers.collection_limits import (
    attach_collection_limits,
    collection_limit,
    limit_payload_list,
    limit_payload_size,
)
from providers.prometheus_analysis import build_metric_analysis

PROMETHEUS_RESULT_KEY = "result"
PROMETHEUS_SAMPLES_KEY = "samples"
PROMETHEUS_SERIES_KEY = "series"
PROMETHEUS_SERIES_VALUES_KEY = "series.values"

MAX_PROMETHEUS_SAMPLES = 250
MAX_PROMETHEUS_SERIES = 100
MAX_PROMETHEUS_SERIES_VALUES = 40
MAX_PROMETHEUS_RESULT_ITEMS = 250


@dataclass(frozen=True)
class PrometheusHttpTarget:
    """Original HTTP authority plus an optional verified, DNS-free connect address."""

    base_url: str
    connect_base_url: str
    host_header: str | None = None
    sni_hostname: str | None = None

    @classmethod
    def build(
        cls,
        base_url: str,
        *,
        resolved_address: str | None = None,
    ) -> PrometheusHttpTarget:
        original = base_url.rstrip("/")
        if resolved_address is None:
            return cls(base_url=original, connect_base_url=original)

        parsed = urlsplit(original)
        hostname = parsed.hostname or ""
        address = ipaddress.ip_address(resolved_address)
        ip_authority = f"[{address}]" if address.version == 6 else str(address)
        if parsed.port is not None:
            ip_authority = f"{ip_authority}:{parsed.port}"
        connect_base_url = urlunsplit(
            (parsed.scheme, ip_authority, parsed.path.rstrip("/"), "", "")
        )
        original_authority = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            original_authority = f"{original_authority}:{parsed.port}"
        return cls(
            base_url=original,
            connect_base_url=connect_base_url,
            host_header=original_authority,
            sni_hostname=hostname if parsed.scheme == "https" else None,
        )


@telemetry.source(
    source="prometheus",
    evidence_key="metrics",
    query_type=PrometheusInstantQuery,
    range_query_type=PrometheusRangeQuery,
)
class PrometheusMetricsProvider:
    """Collect metric data from Prometheus.
    It builds the metrics evidence bucket.
    """

    span_name = "prometheus.collect"
    query_count_attribute = "prometheus.query_count"
    result_count_attribute = "prometheus.result_count"
    timeout_seconds = PROMETHEUS_TIMEOUT_SECONDS
    failure_message = "prometheus metrics collection failed"
    queries: tuple[PrometheusInstantQuery | PrometheusRangeQuery, ...] = ()

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        resolved_address: str | None = None,
    ) -> None:
        """Store the Prometheus base URL without a trailing slash."""
        self.target = PrometheusHttpTarget.build(
            base_url,
            resolved_address=resolved_address,
        )
        self.base_url = self.target.base_url
        self.headers = dict(headers or {})

    def request_url(self, path: str) -> str:
        return f"{self.target.connect_base_url}{path}"

    def request_headers(self) -> dict[str, str]:
        headers = {name: value for name, value in self.headers.items() if name.casefold() != "host"}
        if self.target.host_header is not None:
            headers["Host"] = self.target.host_header
        return headers

    def request_extensions(self) -> dict[str, object]:
        if self.target.sni_hostname is None:
            return {}
        return {"sni_hostname": self.target.sni_hostname}

    async def query(
        self,
        client: httpx.AsyncClient,
        telemetry_query: PrometheusInstantQuery | PrometheusRangeQuery,
    ) -> JsonObject:
        """Run the right Prometheus query type for one request."""
        if isinstance(telemetry_query, PrometheusRangeQuery):
            return await self.query_range(client, telemetry_query)
        return await self.query_instant(client, telemetry_query)

    async def query_instant(
        self,
        client: httpx.AsyncClient,
        telemetry_query: PrometheusInstantQuery,
    ) -> JsonObject:
        """Run an instant Prometheus query and return the raw result."""
        with TRACER.start_as_current_span("prometheus.query") as span:
            span.attr("prometheus.query", telemetry_query.promql)
            response = await client.get(
                self.request_url("/api/v1/query"),
                params={"query": telemetry_query.promql},
                headers=self.request_headers(),
                extensions=self.request_extensions(),
            )
            span.http_status(response.status_code)
            response.raise_for_status()
            return response.json()

    async def query_range(
        self,
        client: httpx.AsyncClient,
        telemetry_query: PrometheusRangeQuery,
    ) -> JsonObject:
        """Run a range Prometheus query over a time window."""
        end = time.time()
        start = end - telemetry_query.range_seconds
        step = telemetry_query.step_seconds or max(1, telemetry_query.range_seconds // 30)
        with TRACER.start_as_current_span("prometheus.query_range") as span:
            span.attr("prometheus.query", telemetry_query.promql)
            span.attr("prometheus.range_seconds", telemetry_query.range_seconds)
            response = await client.get(
                self.request_url("/api/v1/query_range"),
                params={
                    "query": telemetry_query.promql,
                    "start": f"{start:.3f}",
                    "end": f"{end:.3f}",
                    "step": str(step),
                },
                headers=self.request_headers(),
                extensions=self.request_extensions(),
            )
            span.http_status(response.status_code)
            response.raise_for_status()
            return response.json()

    def empty_results(self) -> JsonObject:
        """Create an empty metrics evidence bucket."""
        return {}

    def append_result(
        self,
        results: JsonObject,
        telemetry_query: PrometheusInstantQuery | PrometheusRangeQuery,
        payload: JsonObject,
    ) -> None:
        """Normalize one metric result and save it by metric name."""
        normalized = self.normalize_payload(payload)
        analysis = build_metric_analysis(
            telemetry_query.metric_name,
            telemetry_query.promql,
            normalized,
        )
        limited = limit_prometheus_payload(normalized)
        results[telemetry_query.metric_name] = {
            "query": telemetry_query.promql,
            **self.query_metadata(telemetry_query),
            **limited,
            **analysis,
        }

    def build_response(self, results: JsonObject) -> JsonObject:
        """Return the finished metrics evidence bucket."""
        return {
            "source": self.source,
            "results": results,
        }

    def normalize_payload(self, payload: JsonObject) -> JsonObject:
        """Turn a Prometheus response into samples or series data."""
        data = payload.get("data", {})
        result_type = data.get("resultType")  # vector, matrix, scalar, string
        result = data.get("result", [])

        if result_type == "vector":  # 시계열 값
            samples = []
            for item in result:
                raw_value = item.get("value", [])
                samples.append(
                    {
                        "metric": item.get("metric", {}),
                        "timestamp": raw_value[0] if len(raw_value) >= 1 else None,
                        "value": float(raw_value[1]) if len(raw_value) >= 2 else None,
                    }
                )

            return {
                "result_type": result_type,
                PROMETHEUS_SAMPLES_KEY: samples,
            }

        if result_type == "matrix":  # range query 시계열 값
            series = []
            for item in result:
                values = []
                for raw_value in item.get("values", []):
                    values.append(
                        {
                            "timestamp": raw_value[0] if len(raw_value) >= 1 else None,
                            "value": float(raw_value[1]) if len(raw_value) >= 2 else None,
                        }
                    )
                series.append(
                    {
                        "metric": item.get("metric", {}),
                        "values": values,
                    }
                )

            return {
                "result_type": result_type,
                PROMETHEUS_SERIES_KEY: series,
                "point_count": sum(len(item["values"]) for item in series),
            }

        return {  # 그 외 result type(vector/matrix 아님)
            "result_type": result_type,
            PROMETHEUS_RESULT_KEY: result,
        }

    def query_metadata(
        self,
        telemetry_query: PrometheusInstantQuery | PrometheusRangeQuery,
    ) -> JsonObject:
        """Describe if the query was instant or range based."""
        if not isinstance(telemetry_query, PrometheusRangeQuery):
            return {"query_mode": "instant"}
        return {
            "query_mode": "range",
            "range_seconds": telemetry_query.range_seconds,
            "step_seconds": telemetry_query.step_seconds,
        }


def limit_prometheus_payload(payload: JsonObject) -> JsonObject:
    """Limit large Prometheus result lists while keeping analysis intact."""
    limits: JsonObject = {}
    result_type = payload.get("result_type")
    if result_type == "vector":
        limit_payload_list(payload, PROMETHEUS_SAMPLES_KEY, MAX_PROMETHEUS_SAMPLES, limits)
    elif result_type == "matrix":
        limit_payload_list(payload, PROMETHEUS_SERIES_KEY, MAX_PROMETHEUS_SERIES, limits)
        limit_matrix_series_values(payload, limits)
    else:
        limit_payload_list(payload, PROMETHEUS_RESULT_KEY, MAX_PROMETHEUS_RESULT_ITEMS, limits)
    limit_payload_size(
        payload,
        list_keys=(
            PROMETHEUS_SAMPLES_KEY,
            PROMETHEUS_SERIES_KEY,
            PROMETHEUS_RESULT_KEY,
        ),
        limits=limits,
    )
    if result_type == "matrix":
        sync_matrix_series_value_limit(payload, limits)
    attach_collection_limits(payload, limits)
    return payload


def limit_matrix_series_values(payload: JsonObject, limits: JsonObject) -> None:
    """Limit points inside matrix series and record how much was kept."""
    series = payload.get(PROMETHEUS_SERIES_KEY)
    if not isinstance(series, list):
        return
    original_count = 0
    returned_count = 0
    truncated_series_count = 0
    for item in series:
        if not isinstance(item, dict):
            continue
        values = item.get("values")
        if not isinstance(values, list) or len(values) <= MAX_PROMETHEUS_SERIES_VALUES:
            continue
        limited_values = edge_sample(values, MAX_PROMETHEUS_SERIES_VALUES)
        item["values"] = limited_values
        item["values_truncated"] = True
        item["value_count"] = len(values)
        original_count += len(values)
        returned_count += len(limited_values)
        truncated_series_count += 1
    if truncated_series_count:
        limit = collection_limit(original_count, returned_count)
        limit["series_count"] = truncated_series_count
        limits[PROMETHEUS_SERIES_VALUES_KEY] = limit


def sync_matrix_series_value_limit(payload: JsonObject, limits: JsonObject) -> None:
    """Keep nested matrix value counts aligned with the final series payload."""
    if PROMETHEUS_SERIES_VALUES_KEY not in limits:
        return
    series = payload.get(PROMETHEUS_SERIES_KEY)
    if not isinstance(series, list):
        limits.pop(PROMETHEUS_SERIES_VALUES_KEY, None)
        return
    original_count = 0
    returned_count = 0
    series_count = 0
    for item in series:
        if not isinstance(item, dict):
            continue
        values = item.get("values")
        if not isinstance(values, list):
            continue
        series_count += 1
        returned_count += len(values)
        value_count = item.get("value_count")
        if isinstance(value_count, int) and value_count >= len(values):
            original_count += value_count
        else:
            original_count += len(values)
    if original_count <= returned_count:
        limits.pop(PROMETHEUS_SERIES_VALUES_KEY, None)
        return
    limit = collection_limit(original_count, returned_count)
    limit["series_count"] = series_count
    limits[PROMETHEUS_SERIES_VALUES_KEY] = limit


def edge_sample(values: list[object], max_items: int) -> list[object]:
    """Keep the first and last points from a long time series."""
    if len(values) <= max_items:
        return values
    head_count = max_items // 2
    tail_count = max_items - head_count
    return [*values[:head_count], *values[-tail_count:]]
