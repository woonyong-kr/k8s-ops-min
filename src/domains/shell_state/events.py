"""Auditable web-shell state changes."""

from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject


@event(EventSubject.NAMESPACE_SCOPE_UPDATED)
@dataclass(frozen=True)
class NamespaceScopeUpdatedBody(EventBody):
    workspace_id: str
    user_id: str
    cluster_id: str
    namespaces: list[str]
    expected_revision: int


@event(EventSubject.UI_PREFERENCES_UPDATED)
@dataclass(frozen=True)
class UiPreferencesUpdatedBody(EventBody):
    workspace_id: str
    user_id: str
    preferences: JsonObject
    expected_revision: int
