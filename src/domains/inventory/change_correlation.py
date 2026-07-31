"""Exact inventory-delta correlation stored in existing Timeline metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from packages.contracts.timeline import TimelineEvent

INVENTORY_CHANGE_LEDGER_EPOCH = "inventory_delta_v1"
SOURCE_SNAPSHOT_ID_FIELD = "source_snapshot_id"
REVISION_ID_FIELD = "revision_id"
VERSION_ID_FIELD = "version_id"


class InventoryProjectionCorrelation(Protocol):
    revision_id: int
    version_ids_by_inventory_key: Mapping[str, int]


def correlate_inventory_timeline_events(
    events: Sequence[TimelineEvent],
    *,
    source_snapshot_id: str,
    projection: InventoryProjectionCorrelation,
) -> tuple[TimelineEvent, ...]:
    """Attach exact temporal identities without copying resource payloads into the ledger."""
    snapshot_id = source_snapshot_id.strip()
    if not snapshot_id or projection.revision_id < 1:
        raise ValueError("inventory change correlation is invalid")
    correlated: list[TimelineEvent] = []
    for event in events:
        if event.source != "inventory":
            correlated.append(event)
            continue
        version_id = projection.version_ids_by_inventory_key.get(event.native_id)
        if version_id is None or version_id < 1:
            raise ValueError("inventory change version correlation is unavailable")
        source_key = (
            f"{event.source_key}:snapshot:{snapshot_id}:"
            f"revision:{projection.revision_id}:version:{version_id}"
        )
        correlated.append(
            event.model_copy(
                update={
                    "event_id": source_key,
                    "source_key": source_key,
                    "metadata": {
                        **event.metadata,
                        SOURCE_SNAPSHOT_ID_FIELD: snapshot_id,
                        REVISION_ID_FIELD: projection.revision_id,
                        VERSION_ID_FIELD: version_id,
                    },
                }
            )
        )
    return tuple(correlated)
