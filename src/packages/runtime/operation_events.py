"""Durable command-operation events with Redis used only as a wake-up channel.

Every browser-visible event is first appended to PostgreSQL.  Redis Pub/Sub
announces an already committed row across gateway replicas; it never assigns a
sequence or reconstructs state.  SSE readers replay the database cursor before
using this live fan-out, so a reconnect or a Pub/Sub gap cannot invent state.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.parity import OperationEvent, OperationEventKind

OPERATION_EVENT_CHANNEL_PREFIX = "opsia:operation-events:"
OPERATION_EVENT_QUEUE_MAX = 64
OPERATION_EVENT_RECONNECT_INITIAL_SECONDS = 1.0
OPERATION_EVENT_RECONNECT_MAX_SECONDS = 30.0
RedisFactory = Callable[[str], AsyncRedis]
LOGGER = logging.getLogger(__name__)
_REDIS_TRANSIENT_ERRORS = (
    ConnectionError,
    OSError,
    TimeoutError,
    RedisConnectionError,
    RedisTimeoutError,
)


class OperationEventStore(Protocol):
    """Storage authority required by the production event broker."""

    async def append_command_operation_event(
        self,
        workspace_id: str,
        command_id: str,
        kind: OperationEventKind,
        payload: dict[str, object],
    ) -> OperationEvent | None: ...


class OperationEventStreamOverflow(RuntimeError):
    """A live fan-out queue could not preserve ordering; reconnect from cursor."""


_SubscriptionItem = OperationEvent | OperationEventStreamOverflow


class OperationEventSubscription:
    """One bounded browser subscription; overflow closes rather than drops order."""

    def __init__(
        self,
        command_id: str,
        queue: asyncio.Queue[_SubscriptionItem],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        self.command_id = command_id
        self._queue = queue
        self._close = close
        self._closed = False

    async def next(self) -> OperationEvent:
        item = await self._queue.get()
        if isinstance(item, OperationEventStreamOverflow):
            raise item
        return item

    def empty(self) -> bool:
        return self._queue.empty()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._close()


class OperationEventBroker(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def subscribe(
        self,
        command_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> OperationEventSubscription: ...

    async def announce(
        self,
        event: OperationEvent,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None: ...

    async def publish(
        self,
        *,
        command_id: str,
        kind: OperationEventKind,
        payload: dict[str, object],
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> OperationEvent | None: ...


class InMemoryOperationEventBroker:
    """Deterministic test broker preserving ordering without a persistence fake."""

    def __init__(self) -> None:
        self._sequences: dict[tuple[str, str], int] = defaultdict(int)
        self._terminal: set[tuple[str, str]] = set()
        self._subscriptions: dict[tuple[str, str], set[asyncio.Queue[_SubscriptionItem]]] = (
            defaultdict(set)
        )

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self._subscriptions.clear()

    async def subscribe(
        self,
        command_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> OperationEventSubscription:
        key = _subscription_key(workspace_id, command_id)
        queue: asyncio.Queue[_SubscriptionItem] = asyncio.Queue(maxsize=OPERATION_EVENT_QUEUE_MAX)
        self._subscriptions[key].add(queue)

        async def close() -> None:
            subscribers = self._subscriptions.get(key)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscriptions.pop(key, None)

        return OperationEventSubscription(command_id, queue, close)

    async def publish(
        self,
        *,
        command_id: str,
        kind: OperationEventKind,
        payload: dict[str, object],
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> OperationEvent | None:
        key = _subscription_key(workspace_id, command_id)
        if key in self._terminal:
            return None
        self._sequences[key] += 1
        event = OperationEvent(
            command_id=command_id,
            sequence=self._sequences[key],
            kind=kind,
            payload=payload,
        )
        if kind in {"completed", "failed", "cancelled"}:
            self._terminal.add(key)
        await self.deliver(event, workspace_id=workspace_id)
        return event

    async def deliver(
        self,
        event: OperationEvent,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None:
        for queue in tuple(
            self._subscriptions.get(_subscription_key(workspace_id, event.command_id), ())
        ):
            _offer(queue, event)

    async def announce(
        self,
        event: OperationEvent,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None:
        await self.deliver(event, workspace_id=workspace_id)


class DurableOperationEventBroker:
    """Persist-before-fanout broker used for deterministic storage contract tests."""

    def __init__(self, store: OperationEventStore) -> None:
        self._store = store
        self._local = InMemoryOperationEventBroker()

    async def start(self) -> None:
        await self._local.start()

    async def close(self) -> None:
        await self._local.close()

    async def subscribe(
        self,
        command_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> OperationEventSubscription:
        return await self._local.subscribe(command_id, workspace_id=workspace_id)

    async def publish(
        self,
        *,
        command_id: str,
        kind: OperationEventKind,
        payload: dict[str, object],
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> OperationEvent | None:
        event = await self._store.append_command_operation_event(
            workspace_id,
            command_id,
            kind,
            payload,
        )
        if event is not None:
            await self._local.deliver(event, workspace_id=workspace_id)
        return event

    async def deliver(
        self,
        event: OperationEvent,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None:
        await self._local.deliver(event, workspace_id=workspace_id)

    async def announce(
        self,
        event: OperationEvent,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None:
        await self.deliver(event, workspace_id=workspace_id)


class RedisOperationEventBroker(DurableOperationEventBroker):
    """Cross-replica durable event broker; Pub/Sub carries committed rows only."""

    def __init__(
        self,
        redis_url: str,
        store: OperationEventStore,
        *,
        redis_factory: RedisFactory | None = None,
    ) -> None:
        super().__init__(store)
        self._redis_url = redis_url
        self._redis_factory = redis_factory or _redis_client
        self._client: AsyncRedis | None = None
        self._pubsub: Any | None = None
        self._listener: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._connection_lock = asyncio.Lock()
        self._closed = True

    async def start(self) -> None:
        self._closed = False
        await super().start()
        await self._connect_or_schedule()

    async def close(self) -> None:
        self._closed = True
        reconnect_task = self._reconnect_task
        self._reconnect_task = None
        if reconnect_task is not None:
            reconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconnect_task
        await self._disconnect_client()
        await super().close()

    async def publish(
        self,
        *,
        command_id: str,
        kind: OperationEventKind,
        payload: dict[str, object],
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> OperationEvent | None:
        event = await self._store.append_command_operation_event(
            workspace_id,
            command_id,
            kind,
            payload,
        )
        if event is None:
            return None
        await self.announce(event, workspace_id=workspace_id)
        return event

    async def announce(
        self,
        event: OperationEvent,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None:
        # Local browser clients observe the committed event even while Redis is
        # unavailable. Cross-replica delivery is only an acceleration; SSE
        # replay remains authoritative and heals the missed wake-up.
        await self.deliver(event, workspace_id=workspace_id)
        client = self._client
        if client is None:
            LOGGER.warning(
                "operation_event_redis_unavailable", extra={"command_id": event.command_id}
            )
            self._schedule_reconnect()
            return
        envelope = {
            "workspace_id": workspace_id,
            "event": event.model_dump(mode="json"),
        }
        try:
            await client.publish(_channel(workspace_id, event.command_id), _json(envelope))
        except _REDIS_TRANSIENT_ERRORS:
            LOGGER.warning("operation_event_redis_publish_failed", exc_info=True)
            await self._disconnect_client(client)
            self._schedule_reconnect()

    async def _connect_or_schedule(self) -> None:
        if self._client is not None:
            return
        try:
            await self._connect()
        except _REDIS_TRANSIENT_ERRORS:
            # Redis is a cross-replica wake-up optimization.  Keep the durable
            # PostgreSQL broker live and retry asynchronously instead of failing
            # the gateway lifespan.
            LOGGER.warning("operation_event_redis_connect_failed", exc_info=True)
            self._schedule_reconnect()

    async def _connect(self) -> None:
        async with self._connection_lock:
            if self._closed or self._client is not None:
                return
            client: AsyncRedis | None = None
            pubsub: Any | None = None
            try:
                client = self._redis_factory(self._redis_url)
                await client.ping()
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                await pubsub.psubscribe(f"{OPERATION_EVENT_CHANNEL_PREFIX}*")
            except _REDIS_TRANSIENT_ERRORS:
                await self._close_remote(pubsub, client)
                raise
            if self._closed:
                await self._close_remote(pubsub, client)
                return
            self._client = client
            self._pubsub = pubsub
            self._listener = asyncio.create_task(
                self._listen(pubsub, client), name="operation-event-redis-listener"
            )

    def _schedule_reconnect(self) -> None:
        task = self._reconnect_task
        if self._closed or self._client is not None or (task is not None and not task.done()):
            return
        self._reconnect_task = asyncio.create_task(
            self._reconnect(), name="operation-event-redis-reconnect"
        )

    async def _reconnect(self) -> None:
        delay = OPERATION_EVENT_RECONNECT_INITIAL_SECONDS
        try:
            while not self._closed and self._client is None:
                await asyncio.sleep(delay)
                try:
                    await self._connect()
                except _REDIS_TRANSIENT_ERRORS:
                    LOGGER.warning("operation_event_redis_reconnect_failed", exc_info=True)
                    delay = min(
                        OPERATION_EVENT_RECONNECT_MAX_SECONDS,
                        max(OPERATION_EVENT_RECONNECT_INITIAL_SECONDS, delay * 2),
                    )
                    continue
                if self._client is not None:
                    LOGGER.info("operation_event_redis_reconnected")
                    return
        finally:
            if self._reconnect_task is asyncio.current_task():
                self._reconnect_task = None

    async def _listen(self, pubsub: Any, client: AsyncRedis) -> None:
        try:
            async for raw in pubsub.listen():
                if not isinstance(raw, dict) or raw.get("type") not in {"message", "pmessage"}:
                    continue
                parsed = _parse_envelope(raw.get("data"))
                if parsed is None:
                    continue
                workspace_id, event = parsed
                await self.deliver(event, workspace_id=workspace_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning("operation_event_redis_listener_failed", exc_info=True)
        else:
            LOGGER.warning("operation_event_redis_listener_stopped")
        if not self._closed:
            await self._disconnect_client(client)
            self._schedule_reconnect()

    async def _disconnect_client(self, expected_client: AsyncRedis | None = None) -> None:
        async with self._connection_lock:
            if expected_client is not None and self._client is not expected_client:
                return
            listener = self._listener
            self._listener = None
            pubsub = self._pubsub
            self._pubsub = None
            client = self._client
            self._client = None
        current_task = asyncio.current_task()
        if listener is not None and listener is not current_task:
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
        await self._close_remote(pubsub, client)

    @staticmethod
    async def _close_remote(pubsub: Any | None, client: AsyncRedis | None) -> None:
        close_pubsub = getattr(pubsub, "aclose", None)
        if callable(close_pubsub):
            with suppress(*_REDIS_TRANSIENT_ERRORS):
                await close_pubsub()
        close_client = getattr(client, "aclose", None)
        if callable(close_client):
            with suppress(*_REDIS_TRANSIENT_ERRORS):
                await close_client()


def _redis_client(url: str) -> AsyncRedis:
    return AsyncRedis.from_url(
        url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )


def _subscription_key(workspace_id: str, command_id: str) -> tuple[str, str]:
    return workspace_id.strip(), command_id.strip()


def _channel(workspace_id: str, command_id: str) -> str:
    return f"{OPERATION_EVENT_CHANNEL_PREFIX}{workspace_id}:{command_id}"


def _json(value: dict[str, object]) -> str:
    from json import dumps

    return dumps(value, separators=(",", ":"), ensure_ascii=False)


def _parse_envelope(raw: object) -> tuple[str, OperationEvent] | None:
    from json import loads

    if not isinstance(raw, (bytes, str)):
        return None
    try:
        decoded = loads(raw)
        if not isinstance(decoded, dict):
            return None
        workspace_id = decoded.get("workspace_id")
        event = decoded.get("event")
        if (
            not isinstance(workspace_id, str)
            or not workspace_id.strip()
            or not isinstance(event, dict)
        ):
            return None
        return workspace_id, OperationEvent.model_validate(event)
    except (TypeError, ValueError):
        return None


def _offer(queue: asyncio.Queue[_SubscriptionItem], event: OperationEvent) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # Dropping a middle event would violate replay ordering. Closing this
        # live subscription makes the browser reconnect with Last-Event-ID.
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(OperationEventStreamOverflow("operation event stream overflow"))
