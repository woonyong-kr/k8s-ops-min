from __future__ import annotations

from packages.contracts.event_bus.interfaces import EventEnvelope
from packages.contracts.event_bus.processing import EventProcessingStatus
from packages.contracts.interfaces import EventProcessingRecord, EventProcessingStore


class Ledger:
    def __init__(self, store: EventProcessingStore, consumer: str) -> None:
        self.store = store
        self.consumer = consumer

    def begin(self, evt: EventEnvelope) -> EventProcessingRecord:
        self.store.record_event(evt)
        return self.store.begin_event_processing(evt, self.consumer)

    def finish(self, evt: EventEnvelope, duration_ms: int | None = None) -> None:
        self.store.finish_event_processing(evt, self.consumer, duration_ms)

    def retry(self, evt: EventEnvelope, error: Exception, duration_ms: int | None = None) -> None:
        self.store.fail_event_processing(
            evt, self.consumer, str(error), EventProcessingStatus.RETRYING, duration_ms
        )

    def dead_letter(
        self, evt: EventEnvelope, error: Exception, duration_ms: int | None = None
    ) -> None:
        self.store.fail_event_processing(
            evt, self.consumer, str(error), EventProcessingStatus.DEAD_LETTERED, duration_ms
        )
