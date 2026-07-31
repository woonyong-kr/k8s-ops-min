"""Durable Home dashboard readiness cursors and coalesced commit wake-ups."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from domains.inventory_filter.cursor import CursorScope, FilterCursorCodec
from packages.config.settings import env

DASHBOARD_READY_CURSOR_SURFACE = "home-dashboard-ready"
DASHBOARD_READY_POSITION_TIME = "created_at"
DASHBOARD_READY_POSITION_ID = "snapshot_id"
ORIGIN_SNAPSHOT_ID = "origin"
DASHBOARD_READY_HEARTBEAT_SECONDS_ENV = "DASHBOARD_READY_HEARTBEAT_SECONDS"
DASHBOARD_READY_RECONNECT_AFTER_MS_ENV = "DASHBOARD_READY_RECONNECT_AFTER_MS"
FLEET_SUMMARY_PUSH_INTERVAL_SECONDS_ENV = "FLEET_SUMMARY_PUSH_INTERVAL_SECONDS"


@dataclass(frozen=True)
class DashboardReadySnapshot:
    snapshot_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip() or self.created_at.tzinfo is None:
            raise ValueError("dashboard ready snapshot position is invalid")


@dataclass(frozen=True)
class DashboardReadyCursorBinding:
    workspace_id: str
    cluster_id: str
    user_id: str
    authorization_revision: str

    def as_cursor_scope(self) -> CursorScope:
        identity = f"{self.workspace_id}\0{self.cluster_id}".encode()
        return CursorScope(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            authorization_revision=self.authorization_revision,
            surface=DASHBOARD_READY_CURSOR_SURFACE,
            filter_fingerprint=hashlib.sha256(identity).hexdigest(),
            snapshot_revision=0,
            facet_query=None,
        )


class DashboardReadyCursorCodec:
    def __init__(self, codec: FilterCursorCodec) -> None:
        self._codec = codec

    def encode(
        self,
        snapshot: DashboardReadySnapshot | None,
        *,
        binding: DashboardReadyCursorBinding,
    ) -> str:
        position = snapshot or dashboard_ready_origin()
        return self._codec.encode(
            binding.as_cursor_scope(),
            position={
                DASHBOARD_READY_POSITION_TIME: position.created_at.isoformat(),
                DASHBOARD_READY_POSITION_ID: position.snapshot_id,
            },
        )

    def decode(
        self,
        token: str,
        *,
        binding: DashboardReadyCursorBinding,
    ) -> DashboardReadySnapshot:
        decoded = self._codec.decode(token, expected=binding.as_cursor_scope())
        try:
            created_at = datetime.fromisoformat(
                str(decoded.position[DASHBOARD_READY_POSITION_TIME]).replace("Z", "+00:00")
            )
            snapshot_id = str(decoded.position[DASHBOARD_READY_POSITION_ID]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("dashboard ready cursor position is invalid") from exc
        return DashboardReadySnapshot(snapshot_id=snapshot_id, created_at=created_at)


def dashboard_ready_origin() -> DashboardReadySnapshot:
    return DashboardReadySnapshot(
        snapshot_id=ORIGIN_SNAPSHOT_ID,
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
    )


@dataclass(frozen=True)
class DashboardReadyWakeup:
    workspace_id: str
    cluster_id: str
    snapshot_id: str


class DashboardReadyFanoutClosed(RuntimeError):
    pass


@dataclass(eq=False)
class _SubscriptionState:
    event: asyncio.Event
    closed: bool = False
    latest: DashboardReadyWakeup | None = None


class DashboardReadySubscription:
    def __init__(
        self,
        state: _SubscriptionState,
        dispose: Callable[[_SubscriptionState], None],
    ) -> None:
        self._state = state
        self._dispose = dispose

    async def next(self) -> DashboardReadyWakeup:
        if self._state.closed:
            raise DashboardReadyFanoutClosed("dashboard ready subscription is closed")
        await self._state.event.wait()
        if self._state.closed:
            raise DashboardReadyFanoutClosed("dashboard ready subscription is closed")
        wakeup = self._state.latest
        self._state.latest = None
        self._state.event.clear()
        if wakeup is None:
            raise DashboardReadyFanoutClosed("dashboard ready subscription is closed")
        return wakeup

    async def close(self) -> None:
        self._dispose(self._state)


class InMemoryDashboardReadyFanout:
    """Process-local wake-up only; PostgreSQL snapshots remain the durable source."""

    def __init__(self) -> None:
        self._subscriptions: dict[tuple[str, str], set[_SubscriptionState]] = {}
        self._closed = False

    async def subscribe(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> DashboardReadySubscription:
        key = _scope_key(workspace_id, cluster_id)
        if self._closed:
            raise DashboardReadyFanoutClosed("dashboard ready fanout is closed")
        state = _SubscriptionState(event=asyncio.Event())
        self._subscriptions.setdefault(key, set()).add(state)
        return DashboardReadySubscription(state, self._dispose)

    async def publish_committed(
        self,
        *,
        workspace_id: str,
        cluster_id: str,
        snapshot_id: str,
    ) -> None:
        if self._closed:
            return
        key = _scope_key(workspace_id, cluster_id)
        wakeup = DashboardReadyWakeup(*key, snapshot_id.strip())
        if not wakeup.snapshot_id:
            raise ValueError("dashboard ready snapshot id is required")
        for state in tuple(self._subscriptions.get(key, ())):
            if state.closed:
                continue
            state.latest = wakeup
            state.event.set()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for subscribers in tuple(self._subscriptions.values()):
            for state in tuple(subscribers):
                self._dispose(state)
        self._subscriptions.clear()

    def _dispose(self, state: _SubscriptionState) -> None:
        if state.closed:
            return
        state.closed = True
        state.event.set()
        for key, subscribers in tuple(self._subscriptions.items()):
            subscribers.discard(state)
            if not subscribers:
                self._subscriptions.pop(key, None)


def _scope_key(workspace_id: str, cluster_id: str) -> tuple[str, str]:
    key = workspace_id.strip(), cluster_id.strip()
    if not all(key):
        raise ValueError("dashboard ready scope is required")
    return key


def dashboard_ready_heartbeat_seconds() -> float:
    return _bounded_number(
        DASHBOARD_READY_HEARTBEAT_SECONDS_ENV,
        default="30",
        minimum=1,
        maximum=60,
    )


def dashboard_ready_reconnect_after_ms() -> int:
    return int(
        _bounded_number(
            DASHBOARD_READY_RECONNECT_AFTER_MS_ENV,
            default="1500",
            minimum=100,
            maximum=30_000,
        )
    )


def fleet_summary_push_interval_seconds() -> float:
    """Maximum delay between complete fleet payloads on the workspace stream."""

    return _bounded_number(
        FLEET_SUMMARY_PUSH_INTERVAL_SECONDS_ENV,
        default="5",
        minimum=5,
        maximum=10,
    )


def _bounded_number(name: str, *, default: str, minimum: float, maximum: float) -> float:
    try:
        value = float(env(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value
