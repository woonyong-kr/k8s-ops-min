from __future__ import annotations

from typing import Any, Protocol

import httpx
from span import get_tracer

from packages.contracts.event_bus.interfaces import JsonObject

TRACER = get_tracer("target-cluster-agent.evidence")

ProviderResult = JsonObject | list[JsonObject]


class ConfigReader(Protocol):
    def __call__(self, name: str, default: str) -> str: ...


class TelemetryProvider(Protocol):
    evidence_key: str
    source: str
    span_name: str
    query_count_attribute: str
    result_count_attribute: str
    timeout_seconds: int
    failure_message: str
    queries: tuple[Any, ...]

    @classmethod
    def from_config(cls, read_config: ConfigReader) -> TelemetryProvider: ...

    async def query(
        self,
        client: httpx.AsyncClient,
        telemetry_query: Any,
    ) -> JsonObject: ...

    def empty_results(self) -> Any: ...

    def append_result(
        self,
        results: Any,
        telemetry_query: Any,
        payload: JsonObject,
    ) -> None: ...

    def build_response(self, results: Any) -> ProviderResult: ...
