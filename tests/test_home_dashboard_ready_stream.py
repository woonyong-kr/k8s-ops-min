from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from domains.dashboard.fleet_router import _home_dashboard_sse_body
from domains.dashboard.ready_stream import (
    DashboardReadyCursorBinding,
    DashboardReadyCursorCodec,
    DashboardReadySnapshot,
    InMemoryDashboardReadyFanout,
)
from domains.inventory import ingest as inventory_ingest
from domains.inventory_filter.cursor import FilterCursorCodec


def _binding(cluster_id: str = "cluster-a") -> DashboardReadyCursorBinding:
    return DashboardReadyCursorBinding(
        workspace_id="workspace-a",
        cluster_id=cluster_id,
        user_id="user-a",
        authorization_revision="auth-revision-a",
    )


def _codec() -> DashboardReadyCursorCodec:
    return DashboardReadyCursorCodec(
        FilterCursorCodec("home-dashboard-ready-test-secret-0001", now=lambda: 1_000)
    )


def _frame(raw: str) -> tuple[str, dict[str, Any]]:
    lines = dict(line.split(": ", 1) for line in raw.strip().splitlines() if ": " in line)
    return lines["event"], json.loads(lines["data"])


def test_dashboard_ready_cursor_is_bound_to_cluster_scope() -> None:
    snapshot = DashboardReadySnapshot(
        snapshot_id="snapshot-a",
        created_at=datetime(2026, 7, 24, 4, 0, tzinfo=UTC),
    )
    codec = _codec()
    token = codec.encode(snapshot, binding=_binding("cluster-a"))

    assert codec.decode(token, binding=_binding("cluster-a")) == snapshot
    with pytest.raises(ValueError, match="cursor scope changed"):
        codec.decode(token, binding=_binding("cluster-b"))


def test_home_stream_replays_committed_snapshots_after_connected_frame() -> None:
    async def scenario() -> None:
        first = DashboardReadySnapshot(
            snapshot_id="snapshot-a",
            created_at=datetime(2026, 7, 24, 4, 0, tzinfo=UTC),
        )
        second = DashboardReadySnapshot(
            snapshot_id="snapshot-b",
            created_at=first.created_at + timedelta(seconds=1),
        )
        fanout = InMemoryDashboardReadyFanout()
        subscription = await fanout.subscribe("workspace-a", "cluster-a")
        replay_calls = 0

        def replay_reader(**_kwargs: Any) -> tuple[DashboardReadySnapshot, ...]:
            nonlocal replay_calls
            replay_calls += 1
            return (second,) if replay_calls == 1 else ()

        body = _home_dashboard_sse_body(
            replay_reader=replay_reader,
            subscription=subscription,
            binding=_binding(),
            cursor_codec=_codec(),
            after=first,
            reconnect_after_ms=1_500,
            heartbeat_seconds=30,
        )
        try:
            connected_kind, connected = _frame(await anext(body))
            ready_kind, ready = _frame(await anext(body))
        finally:
            await body.aclose()

        assert connected_kind == "connected"
        assert connected["scope"]["workspace_id"] == "workspace-a"
        assert connected["scope"]["cluster_id"] == "cluster-a"
        assert ready_kind == "deferred_ready"
        assert ready["snapshot_id"] == "snapshot-b"
        assert ready["occurred_at"] == "2026-07-24T04:00:01Z"

    asyncio.run(scenario())


def test_home_stream_emits_latest_position_once_on_a_fresh_connection() -> None:
    async def scenario() -> None:
        latest = DashboardReadySnapshot(
            snapshot_id="snapshot-latest",
            created_at=datetime(2026, 7, 24, 4, 0, tzinfo=UTC),
        )
        fanout = InMemoryDashboardReadyFanout()
        subscription = await fanout.subscribe("workspace-a", "cluster-a")
        body = _home_dashboard_sse_body(
            replay_reader=lambda **_kwargs: (),
            subscription=subscription,
            binding=_binding(),
            cursor_codec=_codec(),
            after=latest,
            reconnect_after_ms=1_500,
            heartbeat_seconds=30,
            emit_initial=True,
        )
        try:
            connected_kind, _connected = _frame(await anext(body))
            ready_kind, ready = _frame(await anext(body))
        finally:
            await body.aclose()

        assert connected_kind == "connected"
        assert ready_kind == "deferred_ready"
        assert ready["snapshot_id"] == "snapshot-latest"

    asyncio.run(scenario())


def test_home_stream_heartbeats_without_inventing_a_snapshot() -> None:
    async def scenario() -> None:
        fanout = InMemoryDashboardReadyFanout()
        subscription = await fanout.subscribe("workspace-a", "cluster-a")
        body = _home_dashboard_sse_body(
            replay_reader=lambda **_kwargs: (),
            subscription=subscription,
            binding=_binding(),
            cursor_codec=_codec(),
            after=None,
            reconnect_after_ms=1_500,
            heartbeat_seconds=0.001,
        )
        try:
            await anext(body)
            heartbeat_kind, heartbeat = _frame(await anext(body))
        finally:
            await body.aclose()

        assert heartbeat_kind == "heartbeat"
        assert "snapshot_id" not in heartbeat
        assert "occurred_at" not in heartbeat

    asyncio.run(scenario())


def test_ready_fanout_coalesces_wakeups_but_keeps_latest_durable_position() -> None:
    async def scenario() -> None:
        fanout = InMemoryDashboardReadyFanout()
        subscription = await fanout.subscribe("workspace-a", "cluster-a")
        await fanout.publish_committed(
            workspace_id="workspace-a",
            cluster_id="cluster-a",
            snapshot_id="snapshot-a",
        )
        await fanout.publish_committed(
            workspace_id="workspace-a",
            cluster_id="cluster-a",
            snapshot_id="snapshot-b",
        )

        wakeup = await subscription.next()
        assert wakeup.snapshot_id == "snapshot-b"
        await subscription.close()
        await fanout.close()

    asyncio.run(scenario())


def test_inventory_ingest_publishes_only_after_an_accepted_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed: list[str] = []

    class ReadyFanout:
        async def publish_committed(self, **payload: str) -> None:
            committed.append(payload["snapshot_id"])

    def accepted_persist(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
        return (
            {
                "accepted": True,
                "snapshot_id": "snapshot-a",
                "resource_count": 0,
                "resource_types": [],
            },
            (),
        )

    monkeypatch.setattr(inventory_ingest, "_persist_inventory_snapshot", accepted_persist)

    asyncio.run(
        inventory_ingest.ingest_inventory_snapshot(
            db=object(),
            workspace_id="workspace-a",
            cluster_id="cluster-a",
            agent_id="agent-a",
            payload={},
            ready_fanout=ReadyFanout(),
        )
    )

    assert committed == ["snapshot-a"]


def test_inventory_ingest_does_not_publish_an_ignored_stale_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed: list[str] = []

    class ReadyFanout:
        async def publish_committed(self, **payload: str) -> None:
            committed.append(payload["snapshot_id"])

    def ignored_persist(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
        return (
            {
                "accepted": False,
                "snapshot_id": "snapshot-stale",
                "resource_count": 0,
                "resource_types": [],
            },
            (),
        )

    monkeypatch.setattr(inventory_ingest, "_persist_inventory_snapshot", ignored_persist)

    asyncio.run(
        inventory_ingest.ingest_inventory_snapshot(
            db=object(),
            workspace_id="workspace-a",
            cluster_id="cluster-a",
            agent_id="agent-a",
            payload={},
            ready_fanout=ReadyFanout(),
        )
    )

    assert committed == []
