"""inventory event body 정의."""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.contracts.event_bus.bodies.base import EventBody
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.identity import DEFAULT_WORKSPACE_ID


@event(EventSubject.CLUSTER_INVENTORY_SNAPSHOT_RECORDED)
@dataclass(frozen=True)
class InventorySnapshotRecordedBody(EventBody):
    """cluster.inventory.snapshot.recorded — agent inventory snapshot 영속됨."""

    cluster_id: str
    snapshot_id: str
    agent_id: str
    resource_count: int
    resource_types: list[str] = field(default_factory=list)
    workspace_id: str = DEFAULT_WORKSPACE_ID
