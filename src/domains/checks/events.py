"""Auditable Checks settings mutations."""

from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject


@event(EventSubject.CHECKS_SETTINGS_UPDATED)
@dataclass(frozen=True)
class ChecksSettingsUpdatedBody(EventBody):
    workspace_id: str
    user_id: str
    policy: JsonObject
    expected_revision: int
