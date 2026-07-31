"""Bounded in-process wake stream; PostgreSQL replay remains authoritative."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from packages.contracts.diagnose import DiagnoseEvent, DiagnoseEventSubscription
from packages.contracts.parity import ClusterScope

DIAGNOSE_SUBSCRIBER_QUEUE_LIMIT = 128


class DiagnoseStreamOverflow(RuntimeError):
    """The subscriber must resume from its last durable sequence."""


_StreamItem = DiagnoseEvent | DiagnoseStreamOverflow


class _Subscription(DiagnoseEventSubscription):
    def __init__(
        self,
        queue: asyncio.Queue[_StreamItem],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        self._queue = queue
        self._close = close

    async def next(self) -> DiagnoseEvent:
        item = await self._queue.get()
        if isinstance(item, DiagnoseStreamOverflow):
            raise item
        return item

    async def close(self) -> None:
        await self._close()


class InMemoryDiagnoseEventStream:
    """Low-latency fan-out only; correctness comes from repository replay."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[_StreamItem]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, event: DiagnoseEvent, *, scope: ClusterScope) -> None:
        del scope
        async with self._lock:
            queues = tuple(self._subscribers.get(event.run_id, ()))
        for queue in queues:
            _offer(queue, event)

    async def subscribe(
        self,
        *,
        scope: ClusterScope,
        run_id: str,
    ) -> DiagnoseEventSubscription:
        del scope
        queue: asyncio.Queue[_StreamItem] = asyncio.Queue(maxsize=DIAGNOSE_SUBSCRIBER_QUEUE_LIMIT)
        async with self._lock:
            self._subscribers[run_id].add(queue)

        async def close() -> None:
            async with self._lock:
                subscribers = self._subscribers.get(run_id)
                if subscribers is None:
                    return
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(run_id, None)

        return _Subscription(queue, close)


def _offer(queue: asyncio.Queue[_StreamItem], event: DiagnoseEvent) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(DiagnoseStreamOverflow("Diagnose event stream overflow"))
