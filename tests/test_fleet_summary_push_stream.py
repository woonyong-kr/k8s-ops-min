from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from domains.dashboard import fleet_router
from domains.dashboard.ready_stream import (
    InMemoryDashboardReadyFanout,
    fleet_summary_push_interval_seconds,
)
from domains.gitops.repository import RepoChangeRepository
from domains.inventory_filter.cursor import FilterCursorCodec
from packages.contracts.gateway.responses import (
    FleetClusterSummaryItem,
    FleetSummaryResponse,
    FleetTotals,
)
from packages.contracts.identity import AccessResourceType


def _current() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-a",
        roles=("operator",),
        workspace_id="workspace-a",
    )


def _codec() -> FilterCursorCodec:
    return FilterCursorCodec("fleet-summary-push-test-secret-0001")


def _summary(cluster_ids: tuple[str, ...]) -> FleetSummaryResponse:
    return FleetSummaryResponse(
        clusters=[
            FleetClusterSummaryItem(
                cluster_id=cluster_id,
                name=cluster_id,
                health="healthy",
                pods_running=4,
                pods_total=4,
                nodes_ready=2,
                nodes_total=2,
            )
            for cluster_id in cluster_ids
        ],
        totals=FleetTotals(
            clusters=len(cluster_ids),
            healthy=len(cluster_ids),
        ),
    )


def _sse(raw: str) -> tuple[dict[str, str], dict[str, Any]]:
    fields = dict(line.split(": ", 1) for line in raw.strip().splitlines() if ": " in line)
    return fields, json.loads(fields["data"])


def test_complete_fleet_frame_has_signed_scope_and_full_summary() -> None:
    codec = _codec()
    raw = fleet_router._fleet_summary_sse_frame(
        summary=_summary(("cluster-a", "cluster-b")),
        current=_current(),
        workspace_id="workspace-a",
        allowed_cluster_ids=("cluster-a", "cluster-b"),
        cursor_codec=codec,
        refresh_seconds=5,
        reconnect_after_ms=1_500,
    )
    fields, payload = _sse(raw)
    decoded = codec.inspect(fields["id"])

    assert fields["event"] == "fleet_summary"
    assert fields["retry"] == "1500"
    assert payload["summary"]["totals"]["clusters"] == 2
    assert [item["cluster_id"] for item in payload["summary"]["clusters"]] == [
        "cluster-a",
        "cluster-b",
    ]
    assert payload["refresh_after_ms"] == 5_000
    assert decoded.scope.workspace_id == "workspace-a"
    assert decoded.scope.user_id == "user-a"
    assert decoded.scope.surface == fleet_router.FLEET_STREAM_CURSOR_SURFACE
    assert decoded.position["revision"] == payload["revision"]


def test_workspace_stream_pushes_direct_payload_and_rechecks_authorization(
    monkeypatch: Any,
) -> None:
    class Db:
        allowed = ("cluster-a",)

        def accessible_resource_ids(self, *_args: Any) -> tuple[str, ...]:
            return self.allowed

    db = Db()
    build_calls: list[tuple[str, ...]] = []

    def build_summary(
        _db: Any,
        _workspace_id: str,
        cluster_ids: tuple[str, ...],
        application_ids: tuple[str, ...],
        *,
        include_platform_totals: bool,
    ) -> FleetSummaryResponse:
        assert include_platform_totals is False
        normalized = tuple(cluster_ids)
        assert tuple(application_ids) == normalized
        build_calls.append(normalized)
        return _summary(normalized)

    monkeypatch.setattr(fleet_router, "build_fleet_summary", build_summary)

    async def scenario() -> None:
        fanout = InMemoryDashboardReadyFanout()
        body = fleet_router._fleet_summary_sse_body(
            db=db,
            current=_current(),
            workspace_id="workspace-a",
            fanout=fanout,
            cursor_codec=_codec(),
            refresh_seconds=5,
            reconnect_after_ms=1_500,
        )
        try:
            first_fields, first = _sse(await anext(body))
            assert first_fields["event"] == "fleet_summary"
            assert first["summary"]["clusters"][0]["cluster_id"] == "cluster-a"

            db.allowed = ("cluster-b",)
            await fanout.publish_committed(
                workspace_id="workspace-a",
                cluster_id="cluster-a",
                snapshot_id="snapshot-a",
            )
            _second_fields, second = _sse(await asyncio.wait_for(anext(body), timeout=1))
            assert second["summary"]["clusters"][0]["cluster_id"] == "cluster-b"
        finally:
            await body.aclose()
            await fanout.close()

    asyncio.run(scenario())
    assert build_calls == [("cluster-a",), ("cluster-b",)]


def test_workspace_stream_keeps_cluster_and_application_authority_separate(
    monkeypatch: Any,
) -> None:
    class Db:
        def accessible_resource_ids(
            self,
            _user_id: str,
            _workspace_id: str,
            resource_type: str,
            _permission: str,
        ) -> tuple[str, ...]:
            if resource_type == AccessResourceType.CLUSTER.value:
                return ("cluster-a",)
            if resource_type == AccessResourceType.APPLICATION.value:
                return ("application-z",)
            raise AssertionError(f"unexpected resource type: {resource_type}")

    observed: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def build_summary(
        _db: Any,
        _workspace_id: str,
        cluster_ids: tuple[str, ...],
        application_ids: tuple[str, ...],
        *,
        include_platform_totals: bool,
    ) -> FleetSummaryResponse:
        assert include_platform_totals is False
        observed.append((tuple(cluster_ids), tuple(application_ids)))
        return _summary(tuple(cluster_ids))

    monkeypatch.setattr(fleet_router, "build_fleet_summary", build_summary)

    async def scenario() -> None:
        body = fleet_router._fleet_summary_sse_body(
            db=Db(),
            current=_current(),
            workspace_id="workspace-a",
            fanout=object(),
            cursor_codec=_codec(),
            refresh_seconds=5,
            reconnect_after_ms=1_500,
        )
        try:
            await anext(body)
        finally:
            await body.aclose()

    asyncio.run(scenario())
    assert observed == [(("cluster-a",), ("application-z",))]


def test_fleet_workflow_counts_filter_application_scope_and_fail_closed() -> None:
    statements: list[Any] = []

    class ScalarResult:
        def scalar(self) -> int:
            return 7

    class RepositoryDouble:
        @contextmanager
        def connection(self):
            class Connection:
                def execute(self, statement: Any) -> ScalarResult:
                    statements.append(statement)
                    return ScalarResult()

            yield Connection()

    repository = RepositoryDouble()
    assert (
        RepoChangeRepository.count_open_workflow_approvals(
            repository,
            "workspace-a",
            (),
        )
        == 0
    )
    assert (
        RepoChangeRepository.count_running_workflow_runs(
            repository,
            "workspace-a",
            (),
        )
        == 0
    )
    assert statements == []

    assert (
        RepoChangeRepository.count_open_workflow_approvals(
            repository,
            "workspace-a",
            ("application-z", "application-a"),
        )
        == 7
    )
    assert (
        RepoChangeRepository.count_running_workflow_runs(
            repository,
            "workspace-a",
            ("application-z", "application-a"),
        )
        == 7
    )
    assert len(statements) == 2
    for statement in statements:
        compiled = statement.compile()
        assert "application_id IN" in str(compiled)
        assert compiled.params["application_id_1"] == [
            "application-a",
            "application-z",
        ]


def test_workspace_stream_still_emits_without_process_local_fanout(
    monkeypatch: Any,
) -> None:
    class Db:
        def accessible_resource_ids(self, *_args: Any) -> tuple[str, ...]:
            return ("cluster-a",)

    monkeypatch.setattr(
        fleet_router,
        "build_fleet_summary",
        lambda _db, _workspace_id, cluster_ids, application_ids, *, include_platform_totals: (
            _summary(tuple(cluster_ids))
            if include_platform_totals is False
            and tuple(application_ids) == tuple(cluster_ids)
            else (_ for _ in ()).throw(
                AssertionError("operator must not observe platform totals")
            )
        ),
    )

    async def scenario() -> None:
        body = fleet_router._fleet_summary_sse_body(
            db=Db(),
            current=_current(),
            workspace_id="workspace-a",
            fanout=object(),
            cursor_codec=_codec(),
            refresh_seconds=5,
            reconnect_after_ms=1_500,
        )
        try:
            _fields, payload = _sse(await anext(body))
            assert payload["summary"]["totals"]["clusters"] == 1
        finally:
            await body.aclose()

    asyncio.run(scenario())


def test_workspace_stream_periodically_pushes_complete_latest_state(
    monkeypatch: Any,
) -> None:
    class Db:
        def accessible_resource_ids(self, *_args: Any) -> tuple[str, ...]:
            return ("cluster-a",)

    revisions = iter((1, 2))

    def build_summary(
        _db: Any,
        _workspace_id: str,
        cluster_ids: tuple[str, ...],
        application_ids: tuple[str, ...],
        *,
        include_platform_totals: bool,
    ) -> FleetSummaryResponse:
        assert include_platform_totals is False
        assert tuple(application_ids) == tuple(cluster_ids)
        summary = _summary(tuple(cluster_ids))
        summary.clusters[0].pods_running = next(revisions)
        return summary

    async def skip_wait(
        _subscriptions: tuple[Any, ...],
        *,
        timeout: float,
    ) -> None:
        assert timeout == 5

    monkeypatch.setattr(fleet_router, "build_fleet_summary", build_summary)
    monkeypatch.setattr(fleet_router, "_wait_for_fleet_stream_refresh", skip_wait)

    async def scenario() -> None:
        body = fleet_router._fleet_summary_sse_body(
            db=Db(),
            current=_current(),
            workspace_id="workspace-a",
            fanout=object(),
            cursor_codec=_codec(),
            refresh_seconds=5,
            reconnect_after_ms=1_500,
        )
        try:
            _first_fields, first = _sse(await anext(body))
            _second_fields, second = _sse(await anext(body))
        finally:
            await body.aclose()

        assert first["summary"]["clusters"][0]["pods_running"] == 1
        assert second["summary"]["clusters"][0]["pods_running"] == 2
        assert first["revision"] != second["revision"]

    asyncio.run(scenario())


def test_accessible_fleet_scope_materializes_service_admin_wildcard() -> None:
    class Db:
        def accessible_resource_ids(self, *_args: Any) -> None:
            return None

        def list_workspace_cluster_ids(self, workspace_id: str) -> tuple[str, ...]:
            assert workspace_id == "workspace-a"
            return ("cluster-b", "cluster-a")

    current = SimpleNamespace(
        user_id="admin-a",
        roles=("service_admin",),
        workspace_id="workspace-a",
    )

    assert asyncio.run(
        fleet_router._accessible_fleet_cluster_ids(Db(), current, "workspace-a")
    ) == ("cluster-a", "cluster-b")


def test_service_admin_stream_can_observe_platform_totals(
    monkeypatch: Any,
) -> None:
    class Db:
        def accessible_resource_ids(self, *_args: Any) -> tuple[str, ...]:
            return ("cluster-a",)

    current = SimpleNamespace(
        user_id="admin-a",
        roles=("service_admin",),
        workspace_id="workspace-a",
    )

    def build_summary(
        _db: Any,
        _workspace_id: str,
        cluster_ids: tuple[str, ...],
        application_ids: tuple[str, ...],
        *,
        include_platform_totals: bool,
    ) -> FleetSummaryResponse:
        assert include_platform_totals is True
        assert tuple(application_ids) == tuple(cluster_ids)
        summary = _summary(tuple(cluster_ids))
        summary.totals.dead_letters = 3
        return summary

    monkeypatch.setattr(fleet_router, "build_fleet_summary", build_summary)

    async def scenario() -> None:
        body = fleet_router._fleet_summary_sse_body(
            db=Db(),
            current=current,
            workspace_id="workspace-a",
            fanout=object(),
            cursor_codec=_codec(),
            refresh_seconds=5,
            reconnect_after_ms=1_500,
        )
        try:
            _fields, payload = _sse(await anext(body))
            assert payload["summary"]["totals"]["dead_letters"] == 3
        finally:
            await body.aclose()

    asyncio.run(scenario())


def test_accessible_fleet_scope_fails_closed_for_non_admin_wildcard() -> None:
    class Db:
        def accessible_resource_ids(self, *_args: Any) -> None:
            return None

        def list_workspace_cluster_ids(self, _workspace_id: str) -> tuple[str, ...]:
            raise AssertionError("non-admin scope must not expand a wildcard")

    assert (
        asyncio.run(
            fleet_router._accessible_fleet_cluster_ids(
                Db(),
                _current(),
                "workspace-a",
            )
        )
        == ()
    )


def test_push_interval_is_bounded_to_five_through_ten_seconds(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("FLEET_SUMMARY_PUSH_INTERVAL_SECONDS", raising=False)
    assert fleet_summary_push_interval_seconds() == 5

    monkeypatch.setenv("FLEET_SUMMARY_PUSH_INTERVAL_SECONDS", "10")
    assert fleet_summary_push_interval_seconds() == 10

    monkeypatch.setenv("FLEET_SUMMARY_PUSH_INTERVAL_SECONDS", "4")
    try:
        fleet_summary_push_interval_seconds()
    except RuntimeError as exc:
        assert "between 5 and 10" in str(exc)
    else:
        raise AssertionError("an unsafe sub-five-second stream interval was accepted")
