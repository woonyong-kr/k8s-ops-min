"""Auditable Helm workspace-configuration facts."""

from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.event_bus.bodies.base import EventBody
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject


@event(EventSubject.HELM_CHART_SOURCE_DELETED)
@dataclass(frozen=True)
class HelmChartSourceDeletedBody(EventBody):
    """Safe deletion evidence; credentials never enter an event payload."""

    workspace_id: str
    source_id: str
    provider: str
    name: str
    reference: str


@event(EventSubject.HELM_CHART_SOURCE_REFRESHED)
@dataclass(frozen=True)
class HelmChartSourceRefreshedBody(EventBody):
    """Successful external index refresh evidence without chart or credential payloads."""

    workspace_id: str
    source_id: str
    name: str
    chart_count: int
    observed_at: str
