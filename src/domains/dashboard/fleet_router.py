"""fleet HTTP API — 콘솔 루트 화면용 워크스페이스 전체 클러스터 롤업.

health 판정 규칙(결정적, 단위 테스트로 고정):
- critical: degraded workload 수 > FLEET_DEGRADED_WORKLOAD_THRESHOLD(0) 또는 not-ready node 존재
- warning : restarts_recent > 0 또는 open_incidents > 0
- stale   : 관측값은 있으나 agent 가 online 이 아님
- unknown : pod/node/usage 관측값이 아직 없음
- healthy : 그 외

집계 원천:
- pod/node/workload 상태 — inventory read model(cluster_inventory_resources) 롤업.
  inventory 에 pod/node 행이 없으면 최신 usage 샘플(pod_running 등)로 대체.
- restarts_recent — 최신 usage 샘플 2개의 restart_total 델타(음수는 0, 샘플<2 이면 0).
- cpu_pct/mem_pct — usage 샘플 우선, 없으면 inventory node 실측 비율 평균(둘 다 없으면 None).
- open_incidents — rca_timeline 에서 실제 탐지 이후 OPEN_INCIDENT_STATUSES logical incident 수.
- last_seen_at — agent 상태 → inventory 최근 관측 → usage 샘플 순으로 첫 값.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Collection
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from domains.checks.observation_projection import checks_overview
from domains.cost.observation_projection import cost_overview
from domains.dashboard.home_bands import (
    compose_home_explore_summary,
    compose_home_posture_summary,
    compose_home_topology_preview,
)
from domains.dashboard.ready_stream import (
    DashboardReadyCursorBinding,
    DashboardReadyCursorCodec,
    DashboardReadyFanoutClosed,
    DashboardReadySnapshot,
    dashboard_ready_heartbeat_seconds,
    dashboard_ready_reconnect_after_ms,
    fleet_summary_push_interval_seconds,
)
from domains.gitops.overview_projection import project_gitops_overview
from domains.gitops.overview_query import parse_gitops_overview_filters
from domains.helm.release_projection import helm_release_list
from domains.identity.dependencies import (
    require_cluster_access,
    require_session,
    resolve_allowed_application_ids,
    resolve_allowed_cluster_ids,
)
from domains.inventory.certificate_expiry import certificate_expiry_summary
from domains.inventory.observed_metrics import (
    inventory_metrics_are_fresh,
    inventory_metrics_observed_at,
    inventory_usage_pct,
    usage_pct,
)
from domains.inventory_filter.cursor import CursorScope, FilterCursorCodec, authorization_revision
from domains.inventory_filter.graph import build_resource_graph
from domains.inventory_filter.query import filter_fingerprint, parse_resource_filters
from domains.target.cluster_visibility import is_blocked_test_cluster
from domains.target.router import (
    cluster_connection_status,
)
from domains.traffic.observation_projection import traffic_overview
from packages.config.certificate_expiry import certificate_expiry_warning_seconds
from packages.config.refresh_policies import integral_refresh_after_seconds
from packages.config.settings import env
from packages.contracts.checks.settings import ChecksSettingsPolicy
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.freshness import HomeDashboardEventFrame
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import (
    ClusterNodesSummaryResponse,
    ClusterOpenIncidentItem,
    ClusterSummaryDetailResponse,
    ClusterUsageSnapshot,
    ClusterWarningEventItem,
    ClusterWorkloadHealthItem,
    FleetClusterSummaryItem,
    FleetSummaryResponse,
    FleetSummaryStreamFrame,
    FleetTotals,
    HomeCustomResourceCount,
    HomeCustomResourceSummary,
    HomeHelmSummary,
    HomeInsightCoverage,
    HomeInsightsResponse,
    NodePodsSummaryResponse,
    NodeSummaryItem,
    PodSummaryItem,
)
from packages.contracts.gitops.overview import GitOpsOverviewCoverage, GitOpsOverviewResponse
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission, ServiceRole
from packages.contracts.parity import ClusterScope
from packages.runtime.dependencies import get_dashboard_ready_fanout, get_db

# health 롤업 상수 — degraded workload 가 이 값을 초과하면 critical(0 = 1개라도 있으면).
FLEET_DEGRADED_WORKLOAD_THRESHOLD = 0
INVENTORY_DEGRADATION_MAX_USAGE_LAG = timedelta(minutes=2)
HEALTH_HEALTHY = "healthy"
HEALTH_WARNING = "warning"
HEALTH_CRITICAL = "critical"
HEALTH_STALE = "stale"
HEALTH_UNKNOWN = "unknown"
UNKNOWN_WORKLOAD_HEALTH = "unknown"

FLEET_CLUSTER_LIMIT = 200
WORKLOAD_LIMIT = 500
WARNING_EVENT_LIMIT = 10
WARNING_EVENT_SCAN_LIMIT = 100
OPEN_INCIDENT_LIMIT = 20
NODE_LIMIT = 1000
POD_LIMIT = 1000
HOME_CUSTOM_RESOURCE_LIMIT = 8
HOME_CERTIFICATE_SCAN_LIMIT = 500
HOME_GITOPS_LIMIT = 200
NOT_FOUND_CODE = 404
DASHBOARD_READY_CURSOR_SIGNING_KEY_ENV = "FILTER_CURSOR_SIGNING_KEY"
DASHBOARD_READY_CURSOR_UNAVAILABLE = "dashboard ready cursor is unavailable"
DASHBOARD_READY_CURSOR_INVALID = "dashboard ready cursor is invalid"
DASHBOARD_READY_STREAM_UNAVAILABLE = "dashboard ready stream is unavailable"
DASHBOARD_READY_REPLAY_LIMIT = 100
FLEET_STREAM_CURSOR_SURFACE = "fleet-summary-stream"
FLEET_STREAM_FILTER_FINGERPRINT = hashlib.sha256(b"workspace-fleet").hexdigest()
FLEET_STREAM_COALESCE_SECONDS = 0.25
OBSERVABILITY_SYSTEM_NAMESPACES = {
    "cert-manager",
    "kube-node-lease",
    "kube-public",
    "kube-system",
    "management",
    "monitoring",
    "target",
}
OBSERVABILITY_AGENT_NAME_MARKERS = (
    "cluster-agent",
    "node-collector",
    "opentelemetry",
    "prometheus",
    "loki",
    "tempo",
    "grafana",
    "kube-state-metrics",
)

router = APIRouter()


@router.get(gateway_routes.FLEET_SUMMARY_PATH, response_model=FleetSummaryResponse)
async def fleet_summary(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> FleetSummaryResponse:
    """워크스페이스 fleet 롤업 — 세션 사용자가 읽을 수 있는 클러스터만 포함."""
    workspace_id = _workspace_id(current)
    allowed_cluster_ids, allowed_application_ids = await asyncio.gather(
        _accessible_fleet_cluster_ids(db, current, workspace_id),
        _accessible_fleet_application_ids(db, current, workspace_id),
    )
    return await asyncio.to_thread(
        build_fleet_summary,
        db,
        workspace_id,
        allowed_cluster_ids,
        allowed_application_ids,
        include_platform_totals=_can_observe_platform_totals(current),
    )


@router.get(gateway_routes.FLEET_SUMMARY_EVENTS_PATH)
async def stream_fleet_summary_events(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    dashboard_ready_fanout: Any = Depends(get_dashboard_ready_fanout),
) -> StreamingResponse:
    """Push the complete authorized fleet projection over one workspace SSE.

    The stream is latest-state, not an append-only activity feed. Every
    reconnect starts with a complete current projection, so a disconnected
    browser cannot permanently miss a fleet transition. Process-local commit
    wake-ups accelerate delivery; the bounded periodic emission also covers
    cross-replica commits, authorization changes, and fanout outages.
    """

    workspace_id = _workspace_id(current)
    cursor_codec = _dashboard_ready_cursor_codec(request)
    # Last-Event-ID never grants access and is not required to reconstruct
    # state. Verify a supplied token when possible, then always send a fresh
    # complete projection under the current authorization scope.
    if last_event_id:
        try:
            cursor_codec.inspect(last_event_id)
        except ValueError:
            pass
    return StreamingResponse(
        _fleet_summary_sse_body(
            db=db,
            current=current,
            workspace_id=workspace_id,
            fanout=dashboard_ready_fanout,
            cursor_codec=cursor_codec,
            refresh_seconds=fleet_summary_push_interval_seconds(),
            reconnect_after_ms=dashboard_ready_reconnect_after_ms(),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _fleet_summary_sse_body(
    *,
    db: Any,
    current: Any,
    workspace_id: str,
    fanout: Any,
    cursor_codec: FilterCursorCodec,
    refresh_seconds: float,
    reconnect_after_ms: int,
) -> AsyncIterator[str]:
    subscriptions: dict[str, Any] = {}
    subscribe = getattr(fanout, "subscribe", None)
    try:
        while True:
            allowed_cluster_ids, allowed_application_ids = await asyncio.gather(
                _accessible_fleet_cluster_ids(db, current, workspace_id),
                _accessible_fleet_application_ids(db, current, workspace_id),
            )
            await _sync_fleet_stream_subscriptions(
                subscriptions,
                subscribe=subscribe,
                workspace_id=workspace_id,
                allowed_cluster_ids=allowed_cluster_ids,
            )
            summary = await asyncio.to_thread(
                build_fleet_summary,
                db,
                workspace_id,
                allowed_cluster_ids,
                allowed_application_ids,
                include_platform_totals=_can_observe_platform_totals(current),
            )
            yield _fleet_summary_sse_frame(
                summary=summary,
                current=current,
                workspace_id=workspace_id,
                allowed_cluster_ids=allowed_cluster_ids,
                cursor_codec=cursor_codec,
                refresh_seconds=refresh_seconds,
                reconnect_after_ms=reconnect_after_ms,
            )
            await _wait_for_fleet_stream_refresh(
                tuple(subscriptions.values()),
                timeout=refresh_seconds,
            )
    finally:
        await _close_fleet_stream_subscriptions(subscriptions)


async def _accessible_fleet_cluster_ids(
    db: Any,
    current: Any,
    workspace_id: str,
) -> tuple[str, ...]:
    values = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.CLUSTER_READ.value,
    )
    return tuple(sorted(values))


async def _accessible_fleet_application_ids(
    db: Any,
    current: Any,
    workspace_id: str,
) -> tuple[str, ...]:
    values = await asyncio.to_thread(
        resolve_allowed_application_ids,
        db,
        current,
        workspace_id,
        Permission.APPLICATION_READ.value,
    )
    return tuple(sorted(values))


def _can_observe_platform_totals(current: Any) -> bool:
    return ServiceRole.SERVICE_ADMIN.value in tuple(getattr(current, "roles", ()) or ())


async def _sync_fleet_stream_subscriptions(
    subscriptions: dict[str, Any],
    *,
    subscribe: Any,
    workspace_id: str,
    allowed_cluster_ids: tuple[str, ...],
) -> None:
    desired = set(allowed_cluster_ids)
    for cluster_id in tuple(subscriptions):
        if cluster_id in desired:
            continue
        subscription = subscriptions.pop(cluster_id)
        await subscription.close()
    if not callable(subscribe):
        return
    for cluster_id in allowed_cluster_ids:
        if cluster_id in subscriptions:
            continue
        try:
            subscriptions[cluster_id] = await subscribe(workspace_id, cluster_id)
        except DashboardReadyFanoutClosed:
            return


async def _wait_for_fleet_stream_refresh(
    subscriptions: tuple[Any, ...],
    *,
    timeout: float,
) -> None:
    if not subscriptions:
        await asyncio.sleep(timeout)
        return
    tasks = [asyncio.create_task(subscription.next()) for subscription in subscriptions]
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if done:
            # Multiple clusters often commit one collection cycle together.
            # Consume that short burst before rebuilding the workspace rollup.
            await asyncio.sleep(FLEET_STREAM_COALESCE_SECONDS)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


async def _close_fleet_stream_subscriptions(subscriptions: dict[str, Any]) -> None:
    values = tuple(subscriptions.values())
    subscriptions.clear()
    await asyncio.gather(
        *(subscription.close() for subscription in values),
        return_exceptions=True,
    )


def _fleet_summary_sse_frame(
    *,
    summary: FleetSummaryResponse,
    current: Any,
    workspace_id: str,
    allowed_cluster_ids: tuple[str, ...],
    cursor_codec: FilterCursorCodec,
    refresh_seconds: float,
    reconnect_after_ms: int,
) -> str:
    generated_at = datetime.now(UTC)
    summary_payload = summary.model_dump(mode="json")
    revision = hashlib.sha256(
        json.dumps(
            summary_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cursor_scope = CursorScope(
        workspace_id=workspace_id,
        user_id=str(current.user_id),
        authorization_revision=authorization_revision(
            user_id=str(current.user_id),
            workspace_id=workspace_id,
            roles=tuple(str(role) for role in (getattr(current, "roles", ()) or ())),
            allowed_cluster_ids=allowed_cluster_ids,
            allowed_application_ids=(),
        ),
        surface=FLEET_STREAM_CURSOR_SURFACE,
        filter_fingerprint=FLEET_STREAM_FILTER_FINGERPRINT,
        snapshot_revision=0,
        facet_query=None,
    )
    cursor = cursor_codec.encode(
        cursor_scope,
        position={
            "revision": revision,
            "generated_at": generated_at.isoformat(),
        },
    )
    frame = FleetSummaryStreamFrame(
        cursor=cursor,
        revision=revision,
        generated_at=generated_at,
        refresh_after_ms=int(refresh_seconds * 1_000),
        summary=summary,
    )
    payload = json.dumps(
        frame.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return f"id: {cursor}\nevent: fleet_summary\nretry: {reconnect_after_ms}\ndata: {payload}\n\n"


@router.get(gateway_routes.CLUSTER_SUMMARY_PATH, response_model=ClusterSummaryDetailResponse)
async def cluster_summary_detail(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ClusterSummaryDetailResponse:
    """fleet 타일 클릭 드릴다운 — 기존 클러스터 라우트와 같은 cluster.read 가드."""
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
    )
    detail = await asyncio.to_thread(build_cluster_summary_detail, db, workspace_id, cluster_id)
    if detail is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="cluster not found")
    return detail


@router.get(
    gateway_routes.CLUSTER_HOME_INSIGHTS_PATH,
    response_model=HomeInsightsResponse,
)
async def cluster_home_insights(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> HomeInsightsResponse:
    """Return bounded Home discovery summaries from one authorized inventory scope."""

    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )
    if await asyncio.to_thread(db.get_cluster_registration, workspace_id, cluster_id) is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="cluster not found")
    allowed_application_ids = await asyncio.to_thread(
        _home_allowed_application_ids,
        db,
        current,
        workspace_id,
    )
    return await asyncio.to_thread(
        build_home_insights,
        db,
        workspace_id,
        cluster_id,
        allowed_application_ids=allowed_application_ids,
        user_id=str(getattr(current, "user_id", "") or "") or None,
    )


@router.get(gateway_routes.CLUSTER_HOME_EVENTS_PATH)
async def stream_cluster_home_events(
    cluster_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    dashboard_ready_fanout: Any = Depends(get_dashboard_ready_fanout),
) -> StreamingResponse:
    """Stream only committed snapshot completions for one authorized Home scope."""

    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
    )
    binding = _dashboard_ready_binding(current, workspace_id, cluster_id)
    cursor_codec = DashboardReadyCursorCodec(_dashboard_ready_cursor_codec(request))
    subscribe = getattr(dashboard_ready_fanout, "subscribe", None)
    replay_reader = getattr(db, "list_dashboard_ready_snapshots", None)
    latest_reader = getattr(db, "latest_dashboard_ready_snapshot", None)
    if not callable(subscribe) or not callable(replay_reader) or not callable(latest_reader):
        raise HTTPException(status_code=503, detail=DASHBOARD_READY_STREAM_UNAVAILABLE)
    try:
        subscription = await subscribe(workspace_id, cluster_id)
    except DashboardReadyFanoutClosed as exc:
        raise HTTPException(status_code=503, detail=DASHBOARD_READY_STREAM_UNAVAILABLE) from exc
    try:
        if last_event_id:
            after = cursor_codec.decode(last_event_id, binding=binding)
            emit_initial = False
        else:
            after = await asyncio.to_thread(
                latest_reader,
                workspace_id=workspace_id,
                cluster_id=cluster_id,
            )
            if after is not None and not isinstance(after, DashboardReadySnapshot):
                raise TypeError("dashboard ready latest reader returned an invalid result")
            emit_initial = after is not None
    except (TypeError, ValueError) as exc:
        await subscription.close()
        raise HTTPException(status_code=422, detail=DASHBOARD_READY_CURSOR_INVALID) from exc
    return StreamingResponse(
        _home_dashboard_sse_body(
            replay_reader=replay_reader,
            subscription=subscription,
            binding=binding,
            cursor_codec=cursor_codec,
            after=after,
            reconnect_after_ms=dashboard_ready_reconnect_after_ms(),
            heartbeat_seconds=dashboard_ready_heartbeat_seconds(),
            emit_initial=emit_initial,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _home_dashboard_sse_body(
    *,
    replay_reader: Any,
    subscription: Any,
    binding: DashboardReadyCursorBinding,
    cursor_codec: DashboardReadyCursorCodec,
    after: DashboardReadySnapshot | None,
    reconnect_after_ms: int,
    heartbeat_seconds: float,
    emit_initial: bool = False,
) -> AsyncIterator[str]:
    position = after
    try:
        yield _dashboard_ready_sse_frame(
            HomeDashboardEventFrame(
                kind="connected",
                cursor=cursor_codec.encode(position, binding=binding),
                scope=ClusterScope(
                    workspace_id=binding.workspace_id,
                    cluster_id=binding.cluster_id,
                ),
                reconnect_after_ms=reconnect_after_ms,
            )
        )
        if emit_initial and position is not None:
            yield _dashboard_ready_sse_frame(
                HomeDashboardEventFrame(
                    kind="deferred_ready",
                    cursor=cursor_codec.encode(position, binding=binding),
                    scope=ClusterScope(
                        workspace_id=binding.workspace_id,
                        cluster_id=binding.cluster_id,
                    ),
                    reconnect_after_ms=reconnect_after_ms,
                    snapshot_id=position.snapshot_id,
                    occurred_at=position.created_at,
                )
            )
        while True:
            records = await asyncio.to_thread(
                replay_reader,
                workspace_id=binding.workspace_id,
                cluster_id=binding.cluster_id,
                after=position or _dashboard_ready_origin_for_replay(),
                limit=DASHBOARD_READY_REPLAY_LIMIT,
            )
            if not isinstance(records, (list, tuple)) or any(
                not isinstance(record, DashboardReadySnapshot) for record in records
            ):
                raise TypeError("dashboard ready replay reader returned an invalid result")
            for record in records:
                position = record
                yield _dashboard_ready_sse_frame(
                    HomeDashboardEventFrame(
                        kind="deferred_ready",
                        cursor=cursor_codec.encode(position, binding=binding),
                        scope=ClusterScope(
                            workspace_id=binding.workspace_id,
                            cluster_id=binding.cluster_id,
                        ),
                        reconnect_after_ms=reconnect_after_ms,
                        snapshot_id=record.snapshot_id,
                        occurred_at=record.created_at,
                    )
                )
            if len(records) >= DASHBOARD_READY_REPLAY_LIMIT:
                continue
            try:
                await asyncio.wait_for(subscription.next(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield _dashboard_ready_sse_frame(
                    HomeDashboardEventFrame(
                        kind="heartbeat",
                        cursor=cursor_codec.encode(position, binding=binding),
                        scope=ClusterScope(
                            workspace_id=binding.workspace_id,
                            cluster_id=binding.cluster_id,
                        ),
                        reconnect_after_ms=reconnect_after_ms,
                    )
                )
            except DashboardReadyFanoutClosed:
                return
    finally:
        await subscription.close()


def _dashboard_ready_sse_frame(frame: HomeDashboardEventFrame) -> str:
    payload = json.dumps(
        frame.model_dump(mode="json", exclude_none=True),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return (
        f"id: {frame.cursor}\n"
        f"event: {frame.kind}\n"
        f"retry: {frame.reconnect_after_ms}\n"
        f"data: {payload}\n\n"
    )


def _dashboard_ready_origin_for_replay() -> DashboardReadySnapshot:
    from domains.dashboard.ready_stream import dashboard_ready_origin

    return dashboard_ready_origin()


def _dashboard_ready_binding(
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> DashboardReadyCursorBinding:
    user_id = str(getattr(current, "user_id", "") or "").strip()
    roles = tuple(str(role) for role in (getattr(current, "roles", ()) or ()))
    if not user_id:
        raise HTTPException(status_code=404, detail="cluster not found")
    return DashboardReadyCursorBinding(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        user_id=user_id,
        authorization_revision=authorization_revision(
            user_id=user_id,
            workspace_id=workspace_id,
            roles=roles,
            allowed_cluster_ids=(cluster_id,),
            allowed_application_ids=(),
        ),
    )


def _dashboard_ready_cursor_codec(request: Request) -> FilterCursorCodec:
    configured = getattr(request.app.state, "dashboard_ready_cursor_codec", None)
    if isinstance(configured, FilterCursorCodec):
        return configured
    try:
        return FilterCursorCodec(env(DASHBOARD_READY_CURSOR_SIGNING_KEY_ENV, "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=DASHBOARD_READY_CURSOR_UNAVAILABLE) from exc


@router.get(
    gateway_routes.CLUSTER_NODES_SUMMARY_PATH,
    response_model=ClusterNodesSummaryResponse,
)
async def cluster_nodes_summary(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ClusterNodesSummaryResponse:
    """노드 히트맵 타일 집계 — 기존 cluster.read 가드와 실제 inventory/usage 만 사용."""
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
    )
    summary = await asyncio.to_thread(build_nodes_summary, db, workspace_id, cluster_id)
    if summary is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="cluster not found")
    return summary


@router.get(
    gateway_routes.CLUSTER_NODE_PODS_SUMMARY_PATH,
    response_model=NodePodsSummaryResponse,
)
async def node_pods_summary(
    cluster_id: str,
    node_name: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> NodePodsSummaryResponse:
    """노드 클릭 시 팟 히트맵 타일 집계 — nodeName 배치 정보 기준."""
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
    )
    summary = await asyncio.to_thread(
        build_node_pods_summary, db, workspace_id, cluster_id, node_name
    )
    if summary is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="cluster not found")
    return summary


def build_fleet_summary(
    db: Any,
    workspace_id: str,
    allowed_cluster_ids: Collection[str] | None,
    allowed_application_ids: Collection[str] | None,
    *,
    include_platform_totals: bool = False,
) -> FleetSummaryResponse:
    registrations = [
        cluster
        for cluster in db.list_cluster_registrations(
            workspace_id, cluster_ids=allowed_cluster_ids, limit=FLEET_CLUSTER_LIMIT
        )
        if not _is_blocked_test_cluster(cluster)
    ]
    cluster_ids = {str(cluster["cluster_id"]) for cluster in registrations}
    inventory = db.fleet_inventory_rollup(workspace_id, cluster_ids) if cluster_ids else {}
    fleet_node_reader = getattr(db, "fleet_inventory_nodes", None)
    inventory_nodes = (
        fleet_node_reader(workspace_id, cluster_ids)
        if cluster_ids and callable(fleet_node_reader)
        else {}
    )
    usage = db.latest_cluster_usage_rollups(workspace_id, cluster_ids) if cluster_ids else {}
    open_counts = db.count_open_rca_incidents(workspace_id, cluster_ids) if cluster_ids else {}
    agents = db.latest_cluster_agent_statuses(workspace_id, cluster_ids) if cluster_ids else {}

    items = [
        fleet_cluster_item(
            cluster,
            inventory.get(str(cluster["cluster_id"]), {}),
            usage.get(str(cluster["cluster_id"]), []),
            open_counts.get(str(cluster["cluster_id"]), 0),
            agents.get(str(cluster["cluster_id"])),
            inventory_nodes.get(str(cluster["cluster_id"]), []),
        )
        for cluster in registrations
    ]
    health_counts = {
        HEALTH_HEALTHY: 0,
        HEALTH_WARNING: 0,
        HEALTH_CRITICAL: 0,
        HEALTH_STALE: 0,
        HEALTH_UNKNOWN: 0,
    }
    for item in items:
        health_counts[item.health] = health_counts.get(item.health, 0) + 1
    totals = FleetTotals(
        clusters=len(items),
        healthy=health_counts[HEALTH_HEALTHY],
        warning=health_counts[HEALTH_WARNING],
        critical=health_counts[HEALTH_CRITICAL],
        stale=health_counts[HEALTH_STALE],
        unknown=health_counts[HEALTH_UNKNOWN],
        open_incidents=sum(item.open_incidents for item in items),
        pending_approvals=int(
            db.count_open_workflow_approvals(workspace_id, allowed_application_ids)
        ),
        running_workflows=int(
            db.count_running_workflow_runs(workspace_id, allowed_application_ids)
        ),
        # Dead letters have no workspace column. Only an explicitly authorized
        # platform observer may read the global count; everyone else receives
        # an honest unobserved value rather than a synthetic zero.
        dead_letters=int(db.open_dead_letter_count()) if include_platform_totals else None,
    )
    return FleetSummaryResponse(clusters=items, totals=totals)


def build_cluster_summary_detail(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> ClusterSummaryDetailResponse | None:
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    if registration is None:
        return None

    workload_rows = db.list_inventory_resources(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="workload",
        namespace=None,
        include_deleted=False,
        limit=WORKLOAD_LIMIT,
    )
    workloads: dict[str, list[ClusterWorkloadHealthItem]] = {}
    for row in workload_rows:
        health = str(row.get("health") or UNKNOWN_WORKLOAD_HEALTH)
        workloads.setdefault(health, []).append(workload_health_item(row))

    pod_rows = db.list_inventory_resources(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="pod",
        namespace=None,
        include_deleted=False,
        limit=POD_LIMIT,
    )
    snapshot_getter = getattr(db, "latest_inventory_snapshot", None)
    latest_snapshot = (
        snapshot_getter(workspace_id, cluster_id) if callable(snapshot_getter) else None
    )
    current_snapshot_id = (
        str(latest_snapshot.get("snapshot_id"))
        if isinstance(latest_snapshot, dict) and latest_snapshot.get("snapshot_id")
        else None
    )
    warning_events = current_warning_event_items(
        db.list_recent_warning_events(
            workspace_id,
            cluster_id,
            limit=WARNING_EVENT_SCAN_LIMIT,
        ),
        pods=pod_rows,
        workloads=workload_rows,
        current_snapshot_id=current_snapshot_id,
        limit=WARNING_EVENT_LIMIT,
    )
    open_incidents = [
        open_incident_item(row)
        for row in db.list_open_rca_incidents(workspace_id, cluster_id, limit=OPEN_INCIDENT_LIMIT)
    ]

    rollup = db.fleet_inventory_rollup(workspace_id, {cluster_id}).get(cluster_id, {})
    samples = db.latest_cluster_usage_rollups(workspace_id, {cluster_id}).get(cluster_id, [])
    restarts_recent = restarts_recent_from_samples(samples)
    nodes_ready, nodes_total = _node_counts(rollup, _latest_usage(samples))
    health = rollup_health(
        workloads_degraded=current_workloads_degraded(rollup, samples),
        nodes_ready=nodes_ready,
        nodes_total=nodes_total,
        restarts_recent=restarts_recent,
        open_incidents=len(open_incidents),
        has_observations=has_observations(rollup, samples),
        connection_status=cluster_connection_status(
            db.latest_cluster_agent_statuses(workspace_id, {cluster_id}).get(cluster_id)
        ),
    )
    return ClusterSummaryDetailResponse(
        cluster_id=cluster_id,
        name=str(registration.get("name") or cluster_id),
        health=health,
        workloads=workloads,
        warning_events=warning_events,
        open_incidents=open_incidents,
        usage=usage_snapshot(samples[-1] if samples else None),
    )


def build_home_insights(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    *,
    allowed_application_ids: set[str] | frozenset[str] = frozenset(),
    user_id: str | None = None,
) -> HomeInsightsResponse:
    contexts = db.filter_snapshot_contexts(workspace_id, (cluster_id,))
    context = contexts.get(cluster_id)
    custom_resources = _home_custom_resource_summary(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        context=context,
    )
    certificate_observations = (
        db.list_tls_secret_certificate_observations(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            limit=HOME_CERTIFICATE_SCAN_LIMIT,
        )
        if int((context or {}).get("snapshot_revision") or 0) > 0
        else {"items": [], "has_more": False}
    )
    certificate_expiry = certificate_expiry_summary(
        certificate_observations.get("items", ()),
        cluster_id=cluster_id,
        context=context,
        scan_truncated=bool(certificate_observations.get("has_more")),
        warning_before_seconds=certificate_expiry_warning_seconds(),
    )
    helm_contexts = db.helm_release_observation_contexts(
        workspace_id=workspace_id,
        cluster_ids=(cluster_id,),
    )
    helm_response = helm_release_list(
        db.list_helm_storage_observations(
            workspace_id=workspace_id,
            cluster_ids=(cluster_id,),
            namespaces=(),
        ),
        contexts=helm_contexts,
        agent_statuses=db.latest_cluster_agent_statuses(workspace_id, {cluster_id}),
        selected_cluster_ids=(cluster_id,),
    )
    helm_coverage = HomeInsightCoverage(
        availability=helm_response.coverage.availability,
        observed_at=helm_response.coverage.observed_at,
        reason_codes=helm_response.coverage.reason_codes,
    )
    status_counts: dict[str, int] = {}
    if helm_coverage.availability != "unavailable":
        for release in helm_response.releases:
            if release.status:
                status_counts[release.status] = status_counts.get(release.status, 0) + 1
    topology = compose_home_topology_preview(
        _home_resource_graph(
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            context=context,
            allowed_application_ids=allowed_application_ids,
        ),
        observed_at=_optional_text((context or {}).get("observed_at")),
    )
    traffic = traffic_overview(
        workspace_id=workspace_id,
        contexts=contexts,
        namespace_refs=(),
        selected_cluster_ids=(cluster_id,),
    )
    cost = cost_overview(
        workspace_id=workspace_id,
        contexts=contexts,
        selected_cluster_ids=(cluster_id,),
    )
    checks = _home_checks_overview(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        contexts=contexts,
        user_id=user_id,
    )
    gitops = _home_gitops_overview(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        contexts=contexts,
        allowed_application_ids=allowed_application_ids,
    )
    return HomeInsightsResponse(
        cluster_id=cluster_id,
        topology=topology,
        explore=compose_home_explore_summary(traffic=traffic, cost=cost),
        posture=compose_home_posture_summary(checks=checks, gitops=gitops),
        custom_resources=custom_resources,
        helm=HomeHelmSummary(
            coverage=helm_coverage,
            release_count=(
                None if helm_coverage.availability == "unavailable" else len(helm_response.releases)
            ),
            status_counts=status_counts,
        ),
        certificate_expiry=certificate_expiry,
        refresh_after_seconds=integral_refresh_after_seconds("dashboard"),
    )


def _home_resource_graph(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    context: JsonObject | None,
    allowed_application_ids: set[str] | frozenset[str],
) -> JsonObject | None:
    revision = int((context or {}).get("snapshot_revision") or 0)
    reader = getattr(db, "list_filtered_resources", None)
    if revision <= 0 or not callable(reader):
        return None
    filters = parse_resource_filters(
        clusters=cluster_id,
        namespaces=None,
        applications=None,
        resource_types=None,
        health=None,
        labels=None,
        query=None,
        include_deleted=False,
    )
    result = reader(
        workspace_id=workspace_id,
        allowed_cluster_ids={cluster_id},
        allowed_application_ids=allowed_application_ids,
        filters=filters,
        snapshot_revision=revision,
        position=None,
        limit=gateway_limits.RESOURCE_GRAPH_DEFAULT_NODE_LIMIT,
        graph_priority=True,
    )
    items = list(result.get("items") or ())
    return build_resource_graph(
        items,
        snapshot_revision=revision,
        filter_fingerprint=filter_fingerprint(filters),
        source_complete=bool((context or {}).get("resources_complete")),
        labels_complete=bool((context or {}).get("labels_complete")),
        truncated=bool(result.get("has_more")),
        node_limit=gateway_limits.RESOURCE_GRAPH_DEFAULT_NODE_LIMIT,
        edge_limit=gateway_limits.RESOURCE_GRAPH_DEFAULT_EDGE_LIMIT,
        omitted_node_count=max(0, int(result.get("filtered_count") or 0) - len(items)),
        partial_reason_codes=tuple((context or {}).get("partial_reason_codes") or ()),
    )


def _home_checks_overview(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    contexts: dict[str, JsonObject],
    user_id: str | None,
):
    snapshot_reader = getattr(db, "latest_inventory_snapshot", None)
    settings_reader = getattr(db, "get_checks_settings", None)
    can_read = callable(snapshot_reader) and callable(settings_reader) and bool(user_id)
    snapshot = snapshot_reader(workspace_id, cluster_id) if can_read else None
    persisted_settings = (
        settings_reader(workspace_id=workspace_id, user_id=user_id) if can_read else None
    )
    settings = ChecksSettingsPolicy.model_validate((persisted_settings or {}).get("policy") or {})
    return checks_overview(
        workspace_id=workspace_id,
        contexts=contexts,
        snapshots={cluster_id: snapshot} if isinstance(snapshot, dict) else {},
        namespace_refs=(),
        selected_cluster_ids=(cluster_id,),
        settings=settings,
    )


def _home_gitops_overview(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    contexts: dict[str, JsonObject],
    allowed_application_ids: set[str] | frozenset[str],
) -> GitOpsOverviewResponse:
    reader = getattr(db, "list_gitops_overview", None)
    if not callable(reader):
        return GitOpsOverviewResponse(
            workspace_id=workspace_id,
            scopes=(ClusterScope(workspace_id=workspace_id, cluster_id=cluster_id),),
            items=(),
            kind_counts=(),
            coverage=GitOpsOverviewCoverage(
                state="unavailable",
                registered_count=0,
                controller_count=0,
                returned_count=0,
                reason_codes=("gitops_overview_repository_unavailable",),
            ),
        )
    filters = parse_gitops_overview_filters(
        clusters=cluster_id,
        namespaces=None,
        applications=None,
        providers=None,
        kinds=None,
        labels=None,
        query=None,
    )
    revision = int((contexts.get(cluster_id) or {}).get("snapshot_revision") or 0)
    result = reader(
        workspace_id=workspace_id,
        allowed_cluster_ids={cluster_id},
        allowed_application_ids=allowed_application_ids,
        filters=filters,
        snapshot_revision=revision,
        limit=HOME_GITOPS_LIMIT,
    )
    return project_gitops_overview(
        workspace_id=workspace_id,
        registered_rows=result.get("registered_rows") or (),
        inventory_rows=result.get("inventory_rows") or (),
        snapshot_contexts=contexts,
        has_more=bool(result.get("has_more")),
    )


def _home_allowed_application_ids(db: Any, current: Any, workspace_id: str) -> set[str]:
    try:
        return resolve_allowed_application_ids(
            db,
            current,
            workspace_id,
            Permission.APPLICATION_READ.value,
        )
    except (AttributeError, NotImplementedError):
        return set()


def _home_custom_resource_summary(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    context: JsonObject | None,
) -> HomeCustomResourceSummary:
    revision = int((context or {}).get("snapshot_revision") or 0)
    if revision <= 0:
        return HomeCustomResourceSummary(
            coverage=HomeInsightCoverage(
                availability="unavailable",
                observed_at=_optional_text((context or {}).get("observed_at")),
                reason_codes=(f"inventory_snapshot_unavailable:{cluster_id}",),
            )
        )
    reasons = {
        str(reason)
        for reason in (context or {}).get("partial_reason_codes", ())
        if str(reason).strip()
    }
    if not bool((context or {}).get("resources_complete")):
        reasons.add("source_resources_incomplete")
    counts = db.list_home_custom_resource_counts(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        snapshot_revision=revision,
        limit=HOME_CUSTOM_RESOURCE_LIMIT,
    )
    items = tuple(_home_custom_resource_count(item) for item in counts.get("items", ()))
    return HomeCustomResourceSummary(
        coverage=HomeInsightCoverage(
            availability="partial" if reasons else "available",
            observed_at=_optional_text((context or {}).get("observed_at")),
            reason_codes=tuple(sorted(reasons)),
        ),
        items=items,
        total_kinds=int(counts.get("total_kinds") or 0),
        total_resources=int(counts.get("total_resources") or 0),
        has_more=int(counts.get("total_kinds") or 0) > len(items),
    )


def _split_api_version(value: str) -> tuple[str, str]:
    group, separator, version = value.partition("/")
    if not separator:
        return ("core", group or "unknown")
    return (group or "unknown", version or "unknown")


def _home_custom_resource_count(item: JsonObject) -> HomeCustomResourceCount:
    api_group, version = _split_api_version(str(item["api_version"]))
    return HomeCustomResourceCount(
        api_group=api_group,
        version=version,
        kind=str(item["kind"]),
        count=int(item["count"]),
    )


def build_nodes_summary(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> ClusterNodesSummaryResponse | None:
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    if registration is None:
        return None
    compact_reader = getattr(db, "node_summary_read_model", None)
    compact = (
        compact_reader(workspace_id, cluster_id, limit=NODE_LIMIT)
        if callable(compact_reader)
        else None
    )
    nodes = (
        [dict(node) for node in compact.get("nodes", []) if isinstance(node, dict)]
        if isinstance(compact, dict)
        else db.list_inventory_resources(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource_type="node",
            namespace=None,
            include_deleted=False,
            limit=NODE_LIMIT,
        )
    )
    latest_snapshot = (
        compact.get("snapshot")
        if isinstance(compact, dict) and isinstance(compact.get("snapshot"), dict)
        else db.latest_inventory_snapshot(workspace_id, cluster_id)
    )
    latest_payload = _summary(latest_snapshot or {})
    nested_summary = latest_payload.get("summary")
    latest_summary = dict(nested_summary) if isinstance(nested_summary, dict) else latest_payload
    snapshot_nodes = latest_summary.get("nodes")
    snapshot_pod_counts = snapshot_node_pod_counts(latest_summary)
    if latest_summary.get("live_inventory") is True and isinstance(snapshot_nodes, list):
        observed_node_names = {
            str(item.get("name") or "")
            for item in snapshot_nodes
            if isinstance(item, dict) and item.get("name")
        }
        nodes = [node for node in nodes if str(node.get("name") or "") in observed_node_names]
    # The compact read model deliberately omits every Pod manifest.  A complete
    # accepted snapshot already carries the exact assigned-Pod count per node,
    # which is the first-paint occupancy contract.  Older agents without that
    # field retain the compatibility path below.
    has_snapshot_pod_counts = bool(nodes) and all(
        str(node.get("name") or "") in snapshot_pod_counts for node in nodes
    )
    if isinstance(compact, dict) and has_snapshot_pod_counts:
        pods: list[JsonObject] = []
    else:
        settings = registration.get("settings")
        include_management_namespace = (
            isinstance(settings, dict) and settings.get("cluster_role") == "management"
        )
        pods = observable_workload_pods(
            db.list_inventory_resources(
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                resource_type="pod",
                namespace=None,
                include_deleted=False,
                limit=POD_LIMIT,
            ),
            include_management_namespace=include_management_namespace,
        )
        if latest_summary.get("live_inventory") is True and isinstance(snapshot_nodes, list):
            pods = [
                pod
                for pod in pods
                if str(_summary(pod).get("node_name") or "") in observed_node_names
            ]

    # The accepted snapshot is normally the newest observation and already has
    # the node-only metric slice.  Consult the secondary rollup only when that
    # evidence is absent; this removes another hot-path DB round trip.
    latest_usage = fresh_snapshot_usage(latest_payload)
    if not latest_usage:
        latest_usage = _latest_usage(
            db.latest_cluster_usage_rollups(workspace_id, {cluster_id}, samples_per_cluster=1).get(
                cluster_id, []
            )
        )
    pods = pods_observed_in_latest_usage(pods, latest_usage)
    pod_groups = pods_by_node(pods)
    return ClusterNodesSummaryResponse(
        cluster_id=cluster_id,
        nodes=[
            node_summary_item(
                node,
                pod_groups.get(str(node.get("name") or ""), []),
                latest_usage,
                pods_running_override=snapshot_pod_counts.get(str(node.get("name") or "")),
            )
            for node in nodes
        ],
    )


def build_node_pods_summary(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    node_name: str,
) -> NodePodsSummaryResponse | None:
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    if registration is None:
        return None
    node = db.get_inventory_resource(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="node",
        kind="Node",
        name=node_name,
        namespace=None,
    )
    if node is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="node not found")
    pods = [
        pod
        for pod in db.list_inventory_resources(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource_type="pod",
            namespace=None,
            include_deleted=False,
            limit=POD_LIMIT,
        )
        if pod_node_name(pod) == node_name
    ]

    latest_usage = _latest_usage(
        db.latest_cluster_usage_rollups(workspace_id, {cluster_id}, samples_per_cluster=1).get(
            cluster_id, []
        )
    )
    incident_lookup = latest_open_incident_lookup(db, workspace_id, cluster_id, pods)
    return NodePodsSummaryResponse(
        cluster_id=cluster_id,
        node_name=node_name,
        pods=[pod_summary_item(pod, latest_usage, incident_lookup) for pod in pods],
    )


def observable_workload_pods(
    pods: list[JsonObject],
    *,
    include_management_namespace: bool = False,
) -> list[JsonObject]:
    return [
        pod
        for pod in pods
        if is_observable_workload_pod(
            pod,
            include_management_namespace=include_management_namespace,
        )
    ]


def pods_observed_in_latest_usage(
    pods: list[JsonObject],
    latest_usage: JsonObject,
) -> list[JsonObject]:
    """Bound node counts to the latest live pod identities when they are available.

    Partial inventory snapshots deliberately retain older rows whose absence was not
    proven.  The live usage payload, however, contains the namespaced identities of
    pods observed in the current metrics cut.  Joining against those identities keeps
    a node tile from counting stale historical rows while preserving the inventory
    fallback for agents that do not emit per-pod usage.
    """
    usage_pods = latest_usage.get("pods")
    pod_total = latest_usage.get("pod_total")
    if (
        not isinstance(usage_pods, dict)
        or not isinstance(pod_total, int)
        or isinstance(pod_total, bool)
        or pod_total != len(usage_pods)
    ):
        return pods
    observed = {str(key) for key in usage_pods if str(key).strip()}
    return [pod for pod in pods if pod_identity(pod) in observed]


def pod_identity(pod: JsonObject) -> str:
    summary = _summary(pod)
    namespace = str(pod.get("namespace") or summary.get("namespace") or "default")
    return f"{namespace}/{str(pod.get('name') or '')}"


def is_observable_workload_pod(
    pod: JsonObject,
    *,
    include_management_namespace: bool = False,
) -> bool:
    summary = _summary(pod)
    namespace = str(pod.get("namespace") or summary.get("namespace") or "").lower()
    if namespace in OBSERVABILITY_SYSTEM_NAMESPACES and not (
        include_management_namespace and namespace == "management"
    ):
        return False
    values = (
        pod.get("name"),
        pod.get("kind"),
        summary.get("name"),
        summary.get("owner_name"),
        summary.get("owner_kind"),
    )
    haystack = " ".join(str(value).lower() for value in values if value not in (None, ""))
    return not any(marker in haystack for marker in OBSERVABILITY_AGENT_NAME_MARKERS)


def rollup_health(
    *,
    workloads_degraded: int,
    nodes_ready: int,
    nodes_total: int,
    restarts_recent: int,
    open_incidents: int,
    has_observations: bool = True,
    connection_status: str = "online",
) -> str:
    """결정적 health 롤업 — 모듈 docstring 의 규칙 그대로(순서: unknown → critical → warning → stale → healthy)."""
    if not has_observations:
        return HEALTH_UNKNOWN
    if workloads_degraded > FLEET_DEGRADED_WORKLOAD_THRESHOLD:
        return HEALTH_CRITICAL
    if nodes_total > 0 and nodes_ready < nodes_total:
        return HEALTH_CRITICAL
    if restarts_recent > 0 or open_incidents > 0:
        return HEALTH_WARNING
    if connection_status != "online":
        return HEALTH_STALE
    return HEALTH_HEALTHY


def restarts_recent_from_samples(samples: list[JsonObject]) -> int:
    """최신 usage 샘플 2개의 restart_total 델타 — 샘플이 2개 미만이면 0, 음수(재수집)도 0."""
    if len(samples) < 2:
        return 0
    latest = _int_or_zero((samples[-1].get("usage") or {}).get("restart_total"))
    previous = _int_or_zero((samples[-2].get("usage") or {}).get("restart_total"))
    return max(latest - previous, 0)


def current_workloads_degraded(
    rollup: JsonObject,
    samples: list[JsonObject],
) -> int:
    """Ignore degradation superseded by substantially newer live usage."""

    degraded = _int_or_zero(rollup.get("workloads_degraded"))
    if degraded == 0 or not samples:
        return degraded
    unknown_time = datetime.min.replace(tzinfo=UTC)
    inventory_seen = event_time_sort_key(_optional_text(rollup.get("last_seen_at")))
    usage_seen = event_time_sort_key(_optional_text(samples[-1].get("sampled_at")))
    if (
        inventory_seen != unknown_time
        and usage_seen - inventory_seen > INVENTORY_DEGRADATION_MAX_USAGE_LAG
    ):
        return 0
    return degraded


def fleet_cluster_item(
    cluster: JsonObject,
    rollup: JsonObject,
    samples: list[JsonObject],
    open_incidents: int,
    agent: JsonObject | None,
    inventory_nodes: list[JsonObject] | None = None,
) -> FleetClusterSummaryItem:
    latest_usage = _latest_usage(samples)
    pods_running, pods_total = _pod_counts(rollup, latest_usage)
    nodes_ready, nodes_total = _node_counts(rollup, latest_usage)
    restarts_recent = restarts_recent_from_samples(samples)
    connection_status = cluster_connection_status(agent)
    return FleetClusterSummaryItem(
        cluster_id=str(cluster["cluster_id"]),
        name=str(cluster.get("name") or cluster["cluster_id"]),
        health=rollup_health(
            workloads_degraded=current_workloads_degraded(rollup, samples),
            nodes_ready=nodes_ready,
            nodes_total=nodes_total,
            restarts_recent=restarts_recent,
            open_incidents=open_incidents,
            has_observations=has_observations(rollup, samples),
            connection_status=connection_status,
        ),
        pods_running=pods_running,
        pods_total=pods_total,
        nodes_ready=nodes_ready,
        nodes_total=nodes_total,
        open_incidents=open_incidents,
        restarts_recent=restarts_recent,
        cpu_pct=preferred_usage_pct(
            latest_usage,
            inventory_nodes or [],
            ("cpu_pct", "cpu_percent"),
            ("cpu_ratio",),
        ),
        mem_pct=preferred_usage_pct(
            latest_usage,
            inventory_nodes or [],
            ("mem_pct", "memory_pct"),
            ("mem_ratio", "memory_ratio"),
        ),
        last_seen_at=_last_seen_at(rollup, samples, agent),
    )


def usage_snapshot(sample: JsonObject | None) -> ClusterUsageSnapshot | None:
    if not sample:
        return None
    usage = dict(sample.get("usage") or {})
    return ClusterUsageSnapshot(
        sampled_at=sample.get("sampled_at"),
        pods_running=_int_or_zero(usage.get("pod_running")),
        pods_total=_int_or_zero(usage.get("pod_total")),
        nodes_ready=_int_or_zero(usage.get("node_ready")),
        nodes_total=_int_or_zero(usage.get("node_total")),
        restart_total=_int_or_zero(usage.get("restart_total")),
        cpu_pct=usage_pct(usage, ("cpu_pct", "cpu_percent"), ("cpu_ratio",)),
        mem_pct=usage_pct(usage, ("mem_pct", "memory_pct"), ("mem_ratio", "memory_ratio")),
    )


def preferred_usage_pct(
    latest_usage: JsonObject,
    inventory_nodes: list[JsonObject],
    pct_keys: tuple[str, ...],
    ratio_keys: tuple[str, ...],
) -> float | None:
    """Prefer the usage rollup, then average observed inventory node measurements."""

    measured = usage_pct(latest_usage, pct_keys, ratio_keys)
    if measured is not None:
        return measured
    values = [
        value
        for node in inventory_nodes
        if (value := inventory_usage_pct(node, pct_keys, ratio_keys)) is not None
    ]
    return round(sum(values) / len(values), 1) if values else None


def workload_health_item(row: JsonObject) -> ClusterWorkloadHealthItem:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    return ClusterWorkloadHealthItem(
        name=str(row.get("name") or ""),
        kind=str(row.get("kind") or ""),
        namespace=row.get("namespace"),
        health=str(row.get("health") or UNKNOWN_WORKLOAD_HEALTH),
        # inventory status 는 "ready/desired" 문자열(kubernetes_snapshot._workload_resource).
        ready=str(row.get("status") or ""),
        restarts=_int_or_zero(summary.get("restart_total")),
    )


def warning_event_item(row: JsonObject) -> ClusterWarningEventItem:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    return ClusterWarningEventItem(
        namespace=row.get("namespace"),
        name=str(row.get("name") or ""),
        reason=summary.get("reason"),
        message=summary.get("message"),
        involved_kind=summary.get("involved_kind"),
        involved_name=summary.get("involved_name"),
        count=_int_or_zero(summary.get("count")),
        last_seen_at=_optional_text(
            summary.get("last_timestamp")
            or summary.get("first_timestamp")
            or row.get("last_seen_at")
        ),
    )


def current_warning_event_items(
    rows: list[JsonObject],
    *,
    pods: list[JsonObject],
    workloads: list[JsonObject],
    current_snapshot_id: str | None,
    limit: int,
) -> list[ClusterWarningEventItem]:
    """Return active warning groups, not one card per retained Event object.

    Pod and ReplicaSet Events are accepted only when the involved object belongs to the
    current inventory snapshot. Events from deleted rollout objects therefore remain in
    inventory history but disappear from the active rail. Multiple current pods owned by
    the same workload are grouped by owner/reason (and probe subtype) into one card.
    """
    current_pods = [row for row in pods if row_is_current(row, current_snapshot_id)]
    current_workloads = [row for row in workloads if row_is_current(row, current_snapshot_id)]
    pod_index = resource_index(current_pods, default_kind="Pod")
    workload_index = resource_index(current_workloads)
    grouped: dict[tuple[str, str, str, str, str], JsonObject] = {}

    for row in rows:
        if not row_is_current(row, current_snapshot_id):
            continue
        summary = _summary(row)
        namespace = str(row.get("namespace") or summary.get("namespace") or "")
        involved_kind = str(summary.get("involved_kind") or "")
        involved_name = str(summary.get("involved_name") or "")
        involved_uid = _optional_text(summary.get("involved_uid"))
        target = current_event_target(
            namespace=namespace,
            involved_kind=involved_kind,
            involved_name=involved_name,
            involved_uid=involved_uid,
            summary=summary,
            pod_index=pod_index,
            workload_index=workload_index,
        )
        if target is None:
            continue
        target_kind, target_name = target
        reason = str(summary.get("reason") or "")
        signal = warning_signal_key(str(summary.get("message") or ""))
        key = (namespace, target_kind, target_name, reason, signal)
        event_time = warning_event_time(row)
        existing = grouped.get(key)
        if existing is None:
            identity_parts = [namespace or "cluster", target_kind, target_name, reason]
            if signal:
                identity_parts.append(signal.replace(" ", "-"))
            grouped[key] = {
                "namespace": namespace or None,
                "name": ":".join(identity_parts),
                "reason": reason or None,
                "message": _optional_text(summary.get("message")),
                "involved_kind": target_kind or None,
                "involved_name": target_name or None,
                "count": _int_or_zero(summary.get("count")),
                "last_seen_at": event_time,
            }
            continue
        existing["count"] = _int_or_zero(existing.get("count")) + _int_or_zero(summary.get("count"))
        if event_time_is_newer(event_time, _optional_text(existing.get("last_seen_at"))):
            existing["last_seen_at"] = event_time
            existing["message"] = _optional_text(summary.get("message"))

    ordered = sorted(
        grouped.values(),
        key=lambda item: event_time_sort_key(_optional_text(item.get("last_seen_at"))),
        reverse=True,
    )
    return [ClusterWarningEventItem.model_validate(item) for item in ordered[:limit]]


def row_is_current(row: JsonObject, current_snapshot_id: str | None) -> bool:
    row_snapshot_id = _optional_text(row.get("snapshot_id"))
    if current_snapshot_id is None or row_snapshot_id is None:
        return True
    return row_snapshot_id == current_snapshot_id


def resource_index(
    rows: list[JsonObject],
    *,
    default_kind: str | None = None,
) -> dict[tuple[str, str, str], JsonObject]:
    indexed: dict[tuple[str, str, str], JsonObject] = {}
    for row in rows:
        summary = _summary(row)
        namespace = str(row.get("namespace") or summary.get("namespace") or "")
        kind = str(row.get("kind") or summary.get("kind") or default_kind or "")
        name = str(row.get("name") or summary.get("name") or "")
        if kind and name:
            indexed[(namespace, kind.casefold(), name)] = row
    return indexed


def current_event_target(
    *,
    namespace: str,
    involved_kind: str,
    involved_name: str,
    involved_uid: str | None,
    summary: JsonObject,
    pod_index: dict[tuple[str, str, str], JsonObject],
    workload_index: dict[tuple[str, str, str], JsonObject],
) -> tuple[str, str] | None:
    kind_key = involved_kind.casefold()
    if kind_key == "pod":
        pod = pod_index.get((namespace, "pod", involved_name))
        if pod is None or (involved_uid and _optional_text(pod.get("uid")) != involved_uid):
            return None
        if warning_is_recovered_probe(summary, pod):
            return None
        pod_summary = _summary(pod)
        owner_kind = _optional_text(pod_summary.get("owner_kind"))
        owner_name = _optional_text(pod_summary.get("owner_name"))
        if owner_kind and owner_name:
            return root_workload_owner(
                namespace,
                owner_kind,
                owner_name,
                workload_index,
            )
        return ("Pod", involved_name)
    if kind_key == "replicaset":
        workload = workload_index.get((namespace, kind_key, involved_name))
        if workload is None or str(workload.get("health") or "").casefold() == "healthy":
            return None
        return root_workload_owner(
            namespace,
            involved_kind,
            involved_name,
            workload_index,
        )
    return (involved_kind or "Object", involved_name or "unknown")


def warning_is_recovered_probe(summary: JsonObject, pod: JsonObject) -> bool:
    message = str(summary.get("message") or "").casefold()
    if str(summary.get("reason") or "") != "Unhealthy" or "probe" not in message:
        return False
    pod_summary = _summary(pod)
    conditions = pod_summary.get("conditions")
    if isinstance(conditions, list):
        ready = next(
            (
                condition
                for condition in conditions
                if isinstance(condition, dict) and condition.get("type") == "Ready"
            ),
            None,
        )
        if ready is not None:
            return str(ready.get("status")) == "True"
    return str(pod.get("health") or "").casefold() == "healthy"


def root_workload_owner(
    namespace: str,
    kind: str,
    name: str,
    workload_index: dict[tuple[str, str, str], JsonObject],
) -> tuple[str, str]:
    current = (kind, name)
    visited: set[tuple[str, str]] = set()
    while current not in visited:
        visited.add(current)
        row = workload_index.get((namespace, current[0].casefold(), current[1]))
        if row is None:
            break
        summary = _summary(row)
        owner_kind = _optional_text(summary.get("owner_kind"))
        owner_name = _optional_text(summary.get("owner_name"))
        if not owner_kind or not owner_name:
            break
        current = (owner_kind, owner_name)
    return current


def warning_signal_key(message: str) -> str:
    normalized = message.casefold()
    for signal in ("readiness probe", "liveness probe", "startup probe"):
        if signal in normalized:
            return signal
    return ""


def warning_event_time(row: JsonObject) -> str | None:
    summary = _summary(row)
    return _optional_text(
        summary.get("last_timestamp") or summary.get("first_timestamp") or row.get("last_seen_at")
    )


def event_time_is_newer(candidate: str | None, existing: str | None) -> bool:
    return event_time_sort_key(candidate) > event_time_sort_key(existing)


def event_time_sort_key(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def open_incident_item(row: JsonObject) -> ClusterOpenIncidentItem:
    return ClusterOpenIncidentItem(
        incident_id=str(row.get("incident_id") or row.get("correlation_id") or ""),
        correlation_id=str(row.get("correlation_id") or row.get("incident_id") or ""),
        symptom=_optional_text(row.get("symptom")),
        root_cause=_optional_text(row.get("root_cause")),
        namespace=_optional_text(row.get("namespace")),
        resource_kind=_optional_text(row.get("resource_kind")),
        resource_name=_optional_text(row.get("resource_name")),
        status=str(row.get("status") or ""),
        created_at=_optional_text(row.get("created_at")),
    )


def node_summary_item(
    node: JsonObject,
    pods: list[JsonObject],
    latest_usage: JsonObject,
    *,
    pods_running_override: int | None = None,
) -> NodeSummaryItem:
    summary = _summary(node)
    node_info = summary.get("node_info")
    name = str(node.get("name") or "")
    running = (
        pods_running_override
        if pods_running_override is not None
        else sum(1 for pod in pods if str(pod.get("status") or "") == "Running")
    )
    cpu_pct = resource_usage_pct(
        latest_usage, "nodes", name, ("cpu_pct", "cpu_percent"), ("cpu_ratio",)
    )
    mem_pct = resource_usage_pct(
        latest_usage,
        "nodes",
        name,
        ("mem_pct", "memory_pct"),
        ("mem_ratio", "memory_ratio"),
    )
    # 첫 손실 지점 수정: freshness 창(metrics_kubernetes 20s)이 metrics.k8s.io 원천
    # 타임스탬프 granularity(실측 34~37s)보다 좁아, 실측이 있는데도 null(관측 안 됨)로
    # 오표시됐다. 실측은 버리지 않는다 — 창을 넘긴 마지막 실측은 값 + stale=true 로
    # 정직하게 노출하고, 실측 자체가 없을 때만 null 을 유지한다(합성 없음).
    metrics_observed_at = _optional_text(
        node.get("metrics_observed_at") or summary.get("metrics_observed_at")
    )
    metrics_fresh = inventory_metrics_are_fresh(metrics_observed_at)
    if cpu_pct is None:
        cpu_pct = inventory_usage_pct(node, ("cpu_pct", "cpu_percent"), ("cpu_ratio",))
    if mem_pct is None:
        mem_pct = inventory_usage_pct(
            node, ("mem_pct", "memory_pct"), ("mem_ratio", "memory_ratio")
        )
    metrics_stale = False
    if (cpu_pct is None or mem_pct is None) and metrics_observed_at and not metrics_fresh:
        if cpu_pct is None:
            cpu_pct = usage_pct(summary, ("cpu_pct", "cpu_percent"), ("cpu_ratio",))
        if mem_pct is None:
            mem_pct = usage_pct(summary, ("mem_pct", "memory_pct"), ("mem_ratio", "memory_ratio"))
        metrics_stale = cpu_pct is not None or mem_pct is not None
    return NodeSummaryItem(
        name=name,
        ready=bool(summary.get("ready")) or str(node.get("status") or "") == "Ready",
        health=str(node.get("health") or HEALTH_UNKNOWN),
        kubernetes_version=_optional_text(
            node_info.get("kubeletVersion") if isinstance(node_info, dict) else None
        ),
        pods_running=running,
        pods_capacity=pod_capacity(summary),
        cpu_pct=cpu_pct,
        mem_pct=mem_pct,
        metrics_observed_at=metrics_observed_at,
        metrics_stale=metrics_stale,
        restarts_recent=sum(pod_restarts(pod) for pod in pods),
        conditions=true_node_conditions(summary),
    )


def snapshot_node_pod_counts(summary: JsonObject) -> dict[str, int]:
    """Return only explicit, non-negative node occupancy observations."""

    raw_nodes = summary.get("nodes")
    if not isinstance(raw_nodes, list):
        return {}
    counts: dict[str, int] = {}
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("pod_count")
        if not name or isinstance(value, bool):
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count >= 0:
            counts[name] = count
    return counts


def pod_summary_item(
    pod: JsonObject,
    latest_usage: JsonObject,
    incident_lookup: dict[tuple[str, str], str],
) -> PodSummaryItem:
    summary = _summary(pod)
    namespace = str(pod.get("namespace") or summary.get("namespace") or "default")
    name = str(pod.get("name") or "")
    return PodSummaryItem(
        name=name,
        namespace=namespace,
        phase=str(pod.get("status") or summary.get("phase") or "Unknown"),
        health=str(pod.get("health") or HEALTH_UNKNOWN),
        ready=pod_ready_text(summary),
        restarts=pod_restarts(pod),
        owner_kind=_optional_text(summary.get("owner_kind")),
        owner_name=_optional_text(summary.get("owner_name")),
        cpu_mcores=resource_usage_value(
            latest_usage, "pods", f"{namespace}/{name}", ("cpu_mcores",)
        ),
        mem_mib=resource_usage_value(
            latest_usage,
            "pods",
            f"{namespace}/{name}",
            ("mem_mib", "memory_mib"),
        ),
        incident_correlation_id=incident_lookup.get((namespace, name)),
    )


def pods_by_node(pods: list[JsonObject]) -> dict[str, list[JsonObject]]:
    grouped: dict[str, list[JsonObject]] = {}
    for pod in pods:
        node_name = pod_node_name(pod)
        if node_name:
            grouped.setdefault(node_name, []).append(pod)
    return grouped


def pod_node_name(pod: JsonObject) -> str:
    return str(_summary(pod).get("node_name") or "")


def pod_restarts(pod: JsonObject) -> int:
    return _int_or_zero(_summary(pod).get("restart_total"))


def pod_capacity(summary: JsonObject) -> int:
    for key in ("allocatable", "capacity"):
        value = summary.get(key)
        if isinstance(value, dict):
            parsed = _int_or_zero(value.get("pods"))
            if parsed:
                return parsed
    return _int_or_zero(summary.get("pod_capacity"))


def true_node_conditions(summary: JsonObject) -> list[str]:
    conditions = summary.get("conditions") if isinstance(summary.get("conditions"), list) else []
    names: list[str] = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        condition_type = str(condition.get("type") or "")
        if condition_type and condition_type != "Ready" and str(condition.get("status")) == "True":
            names.append(condition_type)
    return names


def pod_ready_text(summary: JsonObject) -> str:
    containers = summary.get("containers") if isinstance(summary.get("containers"), list) else []
    if not containers:
        ready_condition = next(
            (
                condition
                for condition in summary.get("conditions", [])
                if isinstance(condition, dict) and condition.get("type") == "Ready"
            ),
            {},
        )
        return "1/1" if ready_condition.get("status") == "True" else "0/0"
    ready = sum(
        1 for container in containers if isinstance(container, dict) and container.get("ready")
    )
    return f"{ready}/{len(containers)}"


def resource_usage_pct(
    usage: JsonObject,
    group_key: str,
    resource_key: str,
    pct_keys: tuple[str, ...],
    ratio_keys: tuple[str, ...],
) -> float | None:
    payload = resource_usage_payload(usage, group_key, resource_key)
    return usage_pct(payload, pct_keys, ratio_keys)


def resource_usage_value(
    usage: JsonObject,
    group_key: str,
    resource_key: str,
    keys: tuple[str, ...],
) -> float | None:
    payload = resource_usage_payload(usage, group_key, resource_key)
    for key in keys:
        value = _float_or_none(payload.get(key))
        if value is not None:
            return value
    return None


def resource_usage_payload(usage: JsonObject, group_key: str, resource_key: str) -> JsonObject:
    group = usage.get(group_key)
    if isinstance(group, dict):
        value = group.get(resource_key) or group.get(resource_key.split("/")[-1])
        return dict(value) if isinstance(value, dict) else {}
    if isinstance(group, list):
        for item in group:
            if not isinstance(item, dict):
                continue
            identity = item.get("key") or item.get("name")
            if identity == resource_key or identity == resource_key.split("/")[-1]:
                return dict(item)
    return {}


def fresh_snapshot_usage(snapshot_payload: JsonObject) -> JsonObject:
    """Extract only fresh per-resource metrics from an accepted snapshot envelope."""

    usage = snapshot_payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    filtered: JsonObject = {}
    for group_key in ("nodes", "pods"):
        group = usage.get(group_key)
        if not isinstance(group, dict):
            continue
        fresh_group = {
            str(resource_key): dict(payload)
            for resource_key, payload in group.items()
            if isinstance(payload, dict) and inventory_metrics_observed_at(payload) is not None
        }
        if fresh_group:
            filtered[group_key] = fresh_group
    return filtered


def latest_open_incident_lookup(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    pods: list[JsonObject],
) -> dict[tuple[str, str], str]:
    resources = {
        (
            str(pod.get("namespace") or _summary(pod).get("namespace") or "default"),
            str(pod.get("name") or ""),
        )
        for pod in pods
        if pod.get("name")
    }
    lookup = getattr(db, "latest_open_incidents_by_resource", None)
    if not callable(lookup):
        exact: dict[tuple[str, str], str] = {}
    else:
        exact = lookup(
            workspace_id,
            cluster_id,
            resource_kind="Pod",
            resources=resources,
        )
    open_incidents = getattr(db, "list_open_cluster_incidents", None)
    if not callable(open_incidents):
        return exact
    incidents = open_incidents(
        workspace_id,
        cluster_id,
        limit=OPEN_INCIDENT_LIMIT,
    )
    mapped = dict(exact)
    for incident in incidents:
        correlation_id = str(incident.get("correlation_id") or incident.get("incident_id") or "")
        if not correlation_id:
            continue
        for pod in pods:
            namespace = str(pod.get("namespace") or _summary(pod).get("namespace") or "default")
            name = str(pod.get("name") or "")
            if not name:
                continue
            key = (namespace, name)
            if key in mapped:
                continue
            if incident_matches_pod(incident, pod):
                mapped[key] = correlation_id
    return mapped


def incident_matches_pod(incident: JsonObject, pod: JsonObject) -> bool:
    summary = _summary(pod)
    namespace = str(pod.get("namespace") or summary.get("namespace") or "default")
    incident_namespace = str(incident.get("namespace") or "")
    if incident_namespace and incident_namespace != namespace:
        return False

    resource_name = str(incident.get("resource_name") or "")
    if not resource_name:
        return False
    resource_kind = str(incident.get("resource_kind") or "").casefold()
    pod_name = str(pod.get("name") or "")
    owner_kind = str(summary.get("owner_kind") or pod.get("owner_kind") or "").casefold()
    owner_name = str(summary.get("owner_name") or pod.get("owner_name") or "")
    labels = pod.get("labels") if isinstance(pod.get("labels"), dict) else {}
    label_values = {str(value) for value in labels.values() if value not in (None, "")}

    if resource_kind == "pod":
        return pod_name == resource_name
    if resource_kind == owner_kind and owner_name == resource_name:
        return True
    if resource_kind == "replicaset":
        return owner_name == resource_name or pod_name.startswith(f"{resource_name}-")
    if resource_kind == "deployment":
        return (
            owner_name.startswith(f"{resource_name}-")
            or pod_name.startswith(f"{resource_name}-")
            or resource_name in label_values
        )
    if resource_kind == "service":
        return resource_name in label_values or pod_name.startswith(f"{resource_name}-")
    return owner_name == resource_name or pod_name.startswith(f"{resource_name}-")


def _pod_counts(rollup: JsonObject, latest_usage: JsonObject) -> tuple[int, int]:
    """Prefer the conservative live read model; use a sample only without inventory rows."""

    total = _int_or_zero(rollup.get("pods_total"))
    if total > 0:
        return _int_or_zero(rollup.get("pods_running")), total
    return _int_or_zero(latest_usage.get("pod_running")), _int_or_zero(
        latest_usage.get("pod_total")
    )


def _node_counts(rollup: JsonObject, latest_usage: JsonObject) -> tuple[int, int]:
    total = _int_or_zero(rollup.get("nodes_total"))
    if total > 0:
        return _int_or_zero(rollup.get("nodes_ready")), total
    return _int_or_zero(latest_usage.get("node_ready")), _int_or_zero(
        latest_usage.get("node_total")
    )


def _latest_usage(samples: list[JsonObject]) -> JsonObject:
    if not samples:
        return {}
    usage = samples[-1].get("usage")
    return dict(usage) if isinstance(usage, dict) else {}


def has_observations(rollup: JsonObject, samples: list[JsonObject]) -> bool:
    if any(
        _int_or_zero(rollup.get(key)) > 0
        for key in ("pods_total", "nodes_total", "workloads_total")
    ):
        return True
    latest_usage = _latest_usage(samples)
    return any(_int_or_zero(latest_usage.get(key)) > 0 for key in ("pod_total", "node_total"))


def _last_seen_at(
    rollup: JsonObject,
    samples: list[JsonObject],
    agent: JsonObject | None,
) -> str | None:
    if agent and agent.get("last_seen_at"):
        return str(agent["last_seen_at"])
    if rollup.get("last_seen_at"):
        return str(rollup["last_seen_at"])
    if samples and samples[-1].get("sampled_at"):
        return str(samples[-1]["sampled_at"])
    return None


def _summary(row: JsonObject) -> JsonObject:
    summary = row.get("summary")
    return dict(summary) if isinstance(summary, dict) else {}


def _optional_text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _is_blocked_test_cluster(cluster: JsonObject) -> bool:
    """/clusters 목록과 같은 테스트 클러스터 숨김 기준(cluster_visibility 단일 소스)."""
    return is_blocked_test_cluster(
        str(cluster.get("cluster_id") or ""), str(cluster.get("name") or "")
    )


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _workspace_id(current: Any) -> str:
    return getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
