from __future__ import annotations

from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.requests import StrictModel


class TelemetryQueryCommandPayload(StrictModel):
    query: dict[str, Any]

    def definition_payload(self) -> JsonObject:
        return self.query
