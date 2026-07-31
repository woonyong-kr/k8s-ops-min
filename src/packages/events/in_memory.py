from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from packages.contracts.event_bus.interfaces import (
    EventBus,
    EventConsumerMetrics,
    EventEnvelope,
    EventMessage,
    EventSubscription,
    JsonObject,
)
from packages.contracts.event_bus.subjects import STREAM_NAME
from packages.events.envelope import event


@dataclass(frozen=True)
class _StoredEvent:
    subject: str
    data: bytes


@dataclass(frozen=True)
class _QueuedEvent:
    stored: _StoredEvent
    redelivery: bool = False


class _InMemoryEventMessage:
    def __init__(
        self,
        data: bytes,
        subscription: _InMemoryEventSubscription,
        delivery_id: int,
    ) -> None:
        self.data = data
        self._subscription = subscription
        self._delivery_id = delivery_id
        self._settled = False

    async def ack(self) -> None:
        if self._settled:
            return
        self._settled = True
        self._subscription.ack(self._delivery_id)

    async def nak(self, delay: int = 0) -> None:
        if delay < 0:
            raise ValueError("nak delay must be non-negative")
        if self._settled:
            return
        self._settled = True
        self._subscription.nak(self._delivery_id, delay)


class _InMemoryEventSubscription:
    def __init__(self, subject: str, durable: str) -> None:
        self.subject = subject
        self.durable = durable
        self._queue: asyncio.Queue[_QueuedEvent] = asyncio.Queue()
        self._in_flight: dict[int, _QueuedEvent] = {}
        self._next_delivery_id = 1
        self._redelivered = 0
        self._scheduled = 0
        self._delayed_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def enqueue(self, stored: _StoredEvent) -> None:
        if self._closed:
            return
        self._queue.put_nowait(_QueuedEvent(stored))

    async def fetch(
        self,
        batch: int,
        timeout: float | None = None,
    ) -> tuple[EventMessage, ...]:
        if self._closed:
            raise RuntimeError("in-memory subscription is closed")
        if batch < 1:
            raise ValueError("fetch batch must be positive")

        first = await self._next(timeout)
        queued = [first]
        while len(queued) < batch:
            try:
                queued.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return tuple(self._deliver(item) for item in queued)

    async def _next(self, timeout: float | None) -> _QueuedEvent:
        if timeout is None:
            return await self._queue.get()
        if timeout <= 0:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty as exc:
                raise TimeoutError from exc
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def _deliver(self, queued: _QueuedEvent) -> EventMessage:
        delivery_id = self._next_delivery_id
        self._next_delivery_id += 1
        self._in_flight[delivery_id] = queued
        if queued.redelivery:
            self._redelivered += 1
        return _InMemoryEventMessage(queued.stored.data, self, delivery_id)

    def ack(self, delivery_id: int) -> None:
        self._in_flight.pop(delivery_id, None)

    def nak(self, delivery_id: int, delay: int) -> None:
        queued = self._in_flight.pop(delivery_id, None)
        if queued is None or self._closed:
            return
        redelivery = _QueuedEvent(queued.stored, redelivery=True)
        if delay == 0:
            self._queue.put_nowait(redelivery)
            return

        self._scheduled += 1
        task = asyncio.create_task(self._requeue_after(redelivery, delay))
        self._delayed_tasks.add(task)
        task.add_done_callback(self._delayed_tasks.discard)

    async def _requeue_after(self, queued: _QueuedEvent, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
            if not self._closed:
                self._queue.put_nowait(queued)
        finally:
            self._scheduled -= 1

    def metrics(self) -> EventConsumerMetrics:
        return EventConsumerMetrics(
            stream=STREAM_NAME,
            subject=self.subject,
            durable=self.durable,
            pending=self._queue.qsize() + self._scheduled,
            ack_pending=len(self._in_flight),
            redelivered=self._redelivered,
        )

    async def close(self) -> None:
        self._closed = True
        for task in self._delayed_tasks:
            task.cancel()
        if self._delayed_tasks:
            await asyncio.gather(*self._delayed_tasks, return_exceptions=True)
        self._delayed_tasks.clear()


class InMemoryEventBus(EventBus):
    """Process-local EventConsumerBus implementation for tests and embedded runtimes."""

    def __init__(self) -> None:
        self._connected = False
        self._closed = False
        self._events: list[_StoredEvent] = []
        self._subscriptions: dict[tuple[str, str], _InMemoryEventSubscription] = {}

    async def connect(self) -> None:
        if self._closed:
            raise RuntimeError("in-memory event bus is closed")
        self._connected = True

    async def emit(
        self,
        subject: str,
        source: str,
        payload: JsonObject,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> EventEnvelope:
        evt = event(subject, source, payload, correlation_id, causation_id)
        return await self.publish_envelope(evt)

    async def publish_envelope(self, evt: EventEnvelope) -> EventEnvelope:
        self._require_connected()
        stored = _StoredEvent(evt.subject, json.dumps(evt.to_dict()).encode())
        self._events.append(stored)
        for subscription in self._subscriptions.values():
            if _subject_matches(subscription.subject, evt.subject):
                subscription.enqueue(stored)
        return evt

    async def subscribe(self, subject: str, durable: str) -> EventSubscription:
        self._require_connected()
        key = (subject, durable)
        subscription = self._subscriptions.get(key)
        if subscription is not None:
            return subscription

        subscription = _InMemoryEventSubscription(subject, durable)
        for stored in self._events:
            if _subject_matches(subject, stored.subject):
                subscription.enqueue(stored)
        self._subscriptions[key] = subscription
        return subscription

    async def consumer_metrics(self, subject: str, durable: str) -> EventConsumerMetrics:
        self._require_connected()
        try:
            subscription = self._subscriptions[(subject, durable)]
        except KeyError as exc:
            raise LookupError(f"unknown in-memory consumer: {durable} ({subject})") from exc
        return subscription.metrics()

    async def close(self) -> None:
        if self._closed:
            return
        self._connected = False
        self._closed = True
        await asyncio.gather(
            *(subscription.close() for subscription in self._subscriptions.values())
        )

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("in-memory event bus is not connected")


def _subject_matches(pattern: str, subject: str) -> bool:
    pattern_tokens = pattern.split(".")
    subject_tokens = subject.split(".")
    for index, token in enumerate(pattern_tokens):
        if token == ">":
            return index == len(pattern_tokens) - 1 and index < len(subject_tokens)
        if index >= len(subject_tokens):
            return False
        if token != "*" and token != subject_tokens[index]:
            return False
    return len(pattern_tokens) == len(subject_tokens)
