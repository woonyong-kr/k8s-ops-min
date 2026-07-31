"""Auditable integration configuration facts without credential values."""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.contracts.event_bus.bodies.base import EventBody
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject


@event(EventSubject.PROMETHEUS_INTEGRATION_CONFIGURED)
@dataclass(frozen=True)
class PrometheusIntegrationConfiguredBody(EventBody):
    workspace_id: str
    cluster_id: str
    revision: str
    operation_id: str
    address: str
    submitted_header_keys: list[str] = field(default_factory=list)
    preserve_stored_headers: bool = False
