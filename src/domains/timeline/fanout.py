"""Bounded, process-local wake-up fan-out for already committed timeline facts.

This module is intentionally not a timeline store or a cross-replica broker.
Readers replay PostgreSQL before subscribing and again after an overflow signal.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from domains.timeline.repository import TimelineLedgerAppend

DEFAULT_TIMELINE_FANOUT_QUEUE_MAX = 64


class TimelineFanoutOverflow(RuntimeError):
    """A subscriber must replay the durable ledger before reading live events again."""

    def __init__(self) -> None:
        super().__init__("timeline fanout overflow; replay durable ledger")


class TimelineFanoutClosed(RuntimeError):
    """A subscription or its owning local fan-out has been disposed."""


@dataclass(frozen=True)
class _OverflowSignal:
    pass


@dataclass(frozen=True)
class _ClosedSignal:
    pass


_SubscriptionItem = TimelineLedgerAppend | _OverflowSignal | _ClosedSignal


@dataclass(eq=False)
class _SubscriptionState:
    queue: asyncio.Queue[_SubscriptionItem]
    closed: bool = False
    overflowed: bool = False


class TimelineEventSubscription:
    """One workspace-local, bounded wake-up subscription.

    Returned appends are internal domain values.  The gateway turns their
    ledger records into user-bound opaque cursors; this class never serializes
    or creates a sequence.
    """

    def __init__(
        self,
        state: _SubscriptionState,
        dispose: Callable[[_SubscriptionState], None],
    ) -> None:
        self._state = state
        self._dispose = dispose

    @property
    def closed(self) -> bool:
        return self._state.closed

    def empty(self) -> bool:
        return not self._state.overflowed and self._state.queue.empty()

    async def next(self) -> TimelineLedgerAppend:
        if self._state.closed:
            raise TimelineFanoutClosed("timeline fanout subscription is closed")
        if self._state.overflowed:
            raise TimelineFanoutOverflow()
        item = await self._state.queue.get()
        if isinstance(item, _OverflowSignal):
            raise TimelineFanoutOverflow()
        if isinstance(item, _ClosedSignal):
            raise TimelineFanoutClosed("timeline fanout subscription is closed")
        return item

    async def close(self) -> None:
        self._dispose(self._state)


class InMemoryTimelineEventFanout:
    """Per-workspace local wake-up fan-out for committed, newly inserted ledger rows."""

    def __init__(self, *, queue_max: int = DEFAULT_TIMELINE_FANOUT_QUEUE_MAX) -> None:
        if queue_max < 1:
            raise ValueError("timeline fanout queue max must be positive")
        self._queue_max = queue_max
        self._subscriptions: dict[str, set[_SubscriptionState]] = defaultdict(set)
        self._closed = False

    async def subscribe(self, workspace_id: str) -> TimelineEventSubscription:
        normalized_workspace = workspace_id.strip()
        if not normalized_workspace:
            raise ValueError("timeline fanout workspace is required")
        if self._closed:
            raise TimelineFanoutClosed("timeline fanout is closed")
        state = _SubscriptionState(queue=asyncio.Queue(maxsize=self._queue_max))
        self._subscriptions[normalized_workspace].add(state)
        return TimelineEventSubscription(state, self._dispose)

    async def publish_committed(self, append: TimelineLedgerAppend) -> None:
        """Offer one committed insert without assigning or serializing a sequence."""
        if self._closed or not append.inserted:
            return
        workspace_id = append.event.scope.workspace_id
        for state in tuple(self._subscriptions.get(workspace_id, ())):
            self._offer(state, append)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for subscribers in tuple(self._subscriptions.values()):
            for state in tuple(subscribers):
                self._dispose(state)
        self._subscriptions.clear()

    def _offer(self, state: _SubscriptionState, append: TimelineLedgerAppend) -> None:
        if state.closed or state.overflowed:
            return
        try:
            state.queue.put_nowait(append)
        except asyncio.QueueFull:
            state.overflowed = True
            _replace_queue_with_signal(state.queue, _OverflowSignal())

    def _dispose(self, state: _SubscriptionState) -> None:
        if state.closed:
            return
        state.closed = True
        for workspace_id, subscribers in tuple(self._subscriptions.items()):
            subscribers.discard(state)
            if not subscribers:
                self._subscriptions.pop(workspace_id, None)
        _replace_queue_with_signal(state.queue, _ClosedSignal())


def _replace_queue_with_signal(
    queue: asyncio.Queue[_SubscriptionItem],
    signal: _OverflowSignal | _ClosedSignal,
) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(signal)
