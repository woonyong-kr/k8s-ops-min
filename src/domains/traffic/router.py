"""HTTP boundary for truthful, read-only traffic availability."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from domains.command.events import CommandRequestedBody
from domains.command.router import (
    COMMAND_PRIORITY_HIGH,
    accept_command_with_receipt_stage,
    announce_staged_operation_event,
    command_accepted_response,
    publish_accepted_operation,
    replay_resource_action_receipt,
)
from domains.gitops.events import Diff
from domains.identity.dependencies import (
    require_cluster_access,
    require_session,
    resolve_allowed_cluster_ids,
)
from domains.inventory_filter.cursor import CursorScope, FilterCursorCodec, authorization_revision
from domains.inventory_filter.query import parse_facet_values, parse_filter_axis_values
from domains.traffic.network_policy import (
    NetworkPolicyIdentityChanged,
    NetworkPolicyObservationUnavailable,
    evaluate_network_policy,
)
from domains.traffic.observation_projection import traffic_overview
from domains.traffic.source_projection import traffic_sources_response
from packages.config.constants import RiskLevel
from packages.config.settings import env
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.parity import CommandReceipt
from packages.contracts.traffic.control import (
    TRAFFIC_SOURCE_CONNECT_ACTION,
    TRAFFIC_SOURCE_SELECT_ACTION,
    NetworkPolicyEvaluationResponse,
    TrafficClusterSourceCatalog,
    TrafficSourceCommandRequest,
    TrafficSourcesResponse,
)
from packages.contracts.traffic.observations import (
    MAX_TRAFFIC_PAGE_SIZE,
    TRAFFIC_SINCE_SECONDS,
    TrafficOverviewResponse,
    TrafficSince,
    TrafficSort,
    TrafficSortOrder,
)
from packages.runtime.dependencies import get_db, get_events, get_operation_events

router = APIRouter()

INVALID_SCOPE_DETAIL = "Traffic scope is invalid"
SCOPE_NOT_FOUND_DETAIL = "Traffic scope not found"
TRAFFIC_SOURCE_ACCESS_DENIED = "Traffic source access denied"
TRAFFIC_SOURCE_CAPABILITY_STALE = "Traffic source capability is stale"
TRAFFIC_SOURCE_NOT_AVAILABLE = "Traffic source is not available"
TRAFFIC_SOURCE_ACTION_UNAVAILABLE = "Traffic source action is unavailable"
TRAFFIC_IDEMPOTENCY_KEY_REUSED = "Traffic idempotency key was reused"
NETWORK_POLICY_RESOURCE_STALE = "NetworkPolicy evaluation Pod identity changed"
NETWORK_POLICY_OBSERVATION_UNAVAILABLE = "NetworkPolicy observation is unavailable"
TRAFFIC_CURSOR_UNAVAILABLE = "Traffic cursor is unavailable"
TRAFFIC_NAMESPACE_AUTHORITY_UNAVAILABLE = "Traffic namespace authority is unavailable"
FILTER_CURSOR_SIGNING_KEY_ENV = "FILTER_CURSOR_SIGNING_KEY"
MAX_CURSOR_LENGTH = 8192
TRAFFIC_CACHE_INVALIDATIONS = (
    "traffic.sources",
    "traffic.flows",
    "traffic.overview",
)


@router.get(gateway_routes.TRAFFIC_FLOWS_PATH, response_model=TrafficOverviewResponse)
async def get_traffic_overview(
    request: Request,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    since: TrafficSince = Query(default="5m"),
    protocols: str | None = Query(default=None),
    verdicts: str | None = Query(default=None),
    sort: TrafficSort = Query(default="connections"),
    order: TrafficSortOrder = Query(default="desc"),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=50, ge=1, le=MAX_TRAFFIC_PAGE_SIZE),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> TrafficOverviewResponse:
    """Return one evidence-backed, scope-bound flow page."""

    requested_clusters = _selected("clusters", clusters)
    requested_namespaces = _namespace_refs(namespaces)
    selected_protocols = _traffic_values(
        "protocols",
        protocols,
        {"tcp", "udp", "http", "grpc", "dns", "unknown"},
    )
    selected_verdicts = _traffic_values(
        "verdicts",
        verdicts,
        {"forwarded", "dropped", "error", "unknown"},
    )
    workspace_id = _workspace_id(current)
    allowed_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    requested_scope_clusters = set(requested_clusters) | {
        cluster_id for cluster_id, _namespace in requested_namespaces
    }
    _require_requested_clusters(requested_scope_clusters, allowed_clusters)
    selected_clusters = tuple(sorted(requested_scope_clusters or allowed_clusters))
    roles = tuple(getattr(current, "roles", ()) or ())
    user_id = str(getattr(current, "user_id", ""))
    authorization = authorization_revision(
        user_id=user_id,
        workspace_id=workspace_id,
        roles=roles,
        allowed_cluster_ids=allowed_clusters,
        allowed_application_ids=(),
    )
    fingerprint = _traffic_filter_fingerprint(
        clusters=selected_clusters,
        namespaces=requested_namespaces,
        since=since,
        protocols=selected_protocols,
        verdicts=selected_verdicts,
        sort=sort,
        order=order,
        limit=limit,
    )
    codec = _cursor_codec(request)
    if cursor is None:
        evidence_revision = int(datetime.now(UTC).timestamp() * 1_000)
        offset = 0
    else:
        try:
            inspected = codec.inspect(cursor)
            cursor_scope = _traffic_cursor_scope(
                workspace_id=workspace_id,
                user_id=user_id,
                authorization=authorization,
                fingerprint=fingerprint,
                evidence_revision=inspected.scope.snapshot_revision,
            )
            decoded = codec.decode(cursor, expected=cursor_scope)
            offset = _traffic_cursor_offset(decoded.position)
            evidence_revision = cursor_scope.snapshot_revision
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL) from exc
    until = datetime.fromtimestamp(evidence_revision / 1_000, tz=UTC)
    since_at = until - timedelta(seconds=_traffic_since_seconds(since))
    contexts, agent_statuses, evidence_windows = await asyncio.gather(
        asyncio.to_thread(
            db.filter_snapshot_contexts,
            workspace_id,
            selected_clusters,
        ),
        asyncio.to_thread(
            _latest_agent_statuses,
            db,
            workspace_id,
            set(selected_clusters),
        ),
        asyncio.to_thread(
            _latest_traffic_evidence_windows,
            db,
            workspace_id,
            set(selected_clusters),
            since_at,
            until,
        ),
    )
    await _require_traffic_namespaces(
        db,
        workspace_id=workspace_id,
        selected_cluster_ids=set(selected_clusters),
        namespace_refs=set(requested_namespaces),
        contexts=contexts,
    )
    next_cursor = codec.encode(
        _traffic_cursor_scope(
            workspace_id=workspace_id,
            user_id=user_id,
            authorization=authorization,
            fingerprint=fingerprint,
            evidence_revision=evidence_revision,
        ),
        position={"offset": offset + limit},
    )
    return traffic_overview(
        workspace_id=workspace_id,
        contexts=contexts,
        namespace_refs=requested_namespaces,
        selected_cluster_ids=selected_clusters,
        evidence_windows=evidence_windows,
        agent_statuses=agent_statuses,
        since=since,
        protocols=selected_protocols,
        verdicts=selected_verdicts,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
        next_cursor=next_cursor,
        now=until,
    )


@router.get(
    gateway_routes.TRAFFIC_SOURCES_PATH,
    response_model=TrafficSourcesResponse,
)
async def get_traffic_sources(
    clusters: str | None = Query(default=None),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> TrafficSourcesResponse:
    """Return only source detector observations reported by authorized target agents."""

    requested_clusters = _selected("clusters", clusters)
    workspace_id = _workspace_id(current)
    allowed_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    _require_requested_clusters(requested_clusters, allowed_clusters)
    selected_clusters = tuple(sorted(requested_clusters or allowed_clusters))
    deploy_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.DEPLOY_RUN.value,
    )
    contexts = await asyncio.to_thread(
        db.filter_snapshot_contexts,
        workspace_id,
        selected_clusters,
    )
    agent_statuses = await asyncio.to_thread(
        _latest_agent_statuses,
        db,
        workspace_id,
        set(selected_clusters),
    )
    return traffic_sources_response(
        workspace_id=workspace_id,
        cluster_ids=selected_clusters,
        contexts=contexts,
        agent_statuses=agent_statuses,
        deploy_cluster_ids=deploy_clusters,
    )


@router.post(
    gateway_routes.TRAFFIC_SOURCE_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def set_traffic_source(
    payload: TrafficSourceCommandRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await _accept_traffic_source_command(
        action=TRAFFIC_SOURCE_SELECT_ACTION,
        action_kind="select",
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.TRAFFIC_CONNECT_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def connect_traffic_source(
    payload: TrafficSourceCommandRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await _accept_traffic_source_command(
        action=TRAFFIC_SOURCE_CONNECT_ACTION,
        action_kind="connect",
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.get(
    gateway_routes.NETWORK_POLICY_EVALUATE_PATH,
    response_model=NetworkPolicyEvaluationResponse,
)
async def evaluate_network_policies(
    cluster: Annotated[str, Query(min_length=1, max_length=255)],
    namespace: Annotated[str, Query(min_length=1, max_length=253)],
    pod_name: Annotated[str, Query(min_length=1, max_length=253)],
    pod_uid: Annotated[str, Query(min_length=1, max_length=255)],
    peer_namespace: Annotated[str, Query(min_length=1, max_length=253)],
    peer_pod_name: Annotated[str, Query(min_length=1, max_length=253)],
    peer_pod_uid: Annotated[str, Query(min_length=1, max_length=255)],
    direction: Literal["ingress", "egress"],
    port: Annotated[int, Query(ge=1, le=65_535)],
    protocol: Literal["TCP", "UDP", "SCTP"] = "TCP",
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> NetworkPolicyEvaluationResponse:
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster,
        Permission.INVENTORY_READ.value,
        detail=TRAFFIC_SOURCE_ACCESS_DENIED,
    )
    try:
        return await asyncio.to_thread(
            evaluate_network_policy,
            db,
            workspace_id=workspace_id,
            cluster_id=cluster,
            namespace=namespace,
            pod_name=pod_name,
            pod_uid=pod_uid,
            peer_namespace=peer_namespace,
            peer_pod_name=peer_pod_name,
            peer_pod_uid=peer_pod_uid,
            direction=direction,
            port=port,
            protocol=protocol,
        )
    except NetworkPolicyIdentityChanged as error:
        raise HTTPException(status_code=409, detail=NETWORK_POLICY_RESOURCE_STALE) from error
    except NetworkPolicyObservationUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=NETWORK_POLICY_OBSERVATION_UNAVAILABLE,
        ) from error


async def _accept_traffic_source_command(
    *,
    action: str,
    action_kind: Literal["select", "connect"],
    payload: TrafficSourceCommandRequest,
    idempotency_key: str,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandReceipt:
    workspace_id = _workspace_id(current)
    if payload.scope.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail=TRAFFIC_SOURCE_ACCESS_DENIED)
    require_cluster_access(
        db,
        current,
        workspace_id,
        payload.scope.cluster_id,
        Permission.DEPLOY_RUN.value,
        detail=TRAFFIC_SOURCE_ACCESS_DENIED,
    )
    fingerprint = _traffic_request_fingerprint(action, payload)
    command_id = _traffic_command_id(
        workspace_id,
        str(getattr(current, "user_id", "")),
        idempotency_key,
    )
    replay = await replay_resource_action_receipt(
        db,
        workspace_id=workspace_id,
        command_id=command_id,
        request_fingerprint=fingerprint,
        idempotency_reused_code=TRAFFIC_IDEMPOTENCY_KEY_REUSED,
    )
    if replay is not None:
        return replay
    catalog = await _current_source_catalog(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=payload.scope.cluster_id,
    )
    if catalog.capability_revision != payload.capability_revision:
        raise HTTPException(status_code=409, detail=TRAFFIC_SOURCE_CAPABILITY_STALE)
    source = next((item for item in catalog.sources if item.key == payload.source_key), None)
    if source is None or source.status != "available":
        raise HTTPException(status_code=409, detail=TRAFFIC_SOURCE_NOT_AVAILABLE)
    descriptor = next((item for item in source.actions if item.kind == action_kind), None)
    already_selected = action_kind == "select" and catalog.active_source == source.key
    if (descriptor is None or not descriptor.enabled) and not already_selected:
        raise HTTPException(status_code=409, detail=TRAFFIC_SOURCE_ACTION_UNAVAILABLE)
    diff = Diff(
        workspace_id=workspace_id,
        cluster_id=payload.scope.cluster_id,
        resource=f"traffic-source/{payload.source_key}",
        namespace="",
        desired_image="",
        actual_image=catalog.active_source or "not-selected",
        risk=RiskLevel.REVIEW_REQUIRED,
        status=action,
        basis={
            "source_key": payload.source_key,
            "capability_revision": payload.capability_revision,
            "request_fingerprint": fingerprint,
            "cache_invalidations": list(TRAFFIC_CACHE_INVALIDATIONS),
        },
    )
    command = CommandRequestedBody(
        cluster_id=payload.scope.cluster_id,
        action=action,
        namespace="",
        reason=payload.reason,
        diff=diff,
        command_id=command_id,
        payload={
            "source_key": payload.source_key,
            "capability_revision": payload.capability_revision,
            "cache_invalidations": list(TRAFFIC_CACHE_INVALIDATIONS),
        },
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(getattr(current, "user_id", "")),
        direct_execution=True,
        direct_execution_confirmed=True,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(
            str(getattr(current, "user_id", "")),
            tuple(getattr(current, "roles", ()) or ()),
        ),
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


async def _current_source_catalog(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> TrafficClusterSourceCatalog:
    contexts = await asyncio.to_thread(
        db.filter_snapshot_contexts,
        workspace_id,
        (cluster_id,),
    )
    statuses = await asyncio.to_thread(
        _latest_agent_statuses,
        db,
        workspace_id,
        {cluster_id},
    )
    response = traffic_sources_response(
        workspace_id=workspace_id,
        cluster_ids=(cluster_id,),
        contexts=contexts,
        agent_statuses=statuses,
        deploy_cluster_ids={cluster_id},
    )
    if not response.clusters:
        raise HTTPException(status_code=409, detail=TRAFFIC_SOURCE_NOT_AVAILABLE)
    return response.clusters[0]


def _traffic_request_fingerprint(
    action: str,
    payload: TrafficSourceCommandRequest,
) -> str:
    document = {
        "action": action,
        "payload": payload.model_dump(mode="json"),
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _traffic_command_id(workspace_id: str, user_id: str, idempotency_key: str) -> str:
    authority = "\0".join((workspace_id, user_id, idempotency_key))
    return f"cmd-traffic-{hashlib.sha256(authority.encode()).hexdigest()[:24]}"


def _latest_agent_statuses(
    db: Any,
    workspace_id: str,
    cluster_ids: set[str],
) -> dict[str, dict[str, Any]]:
    reader = getattr(db, "latest_cluster_agent_statuses", None)
    if not callable(reader):
        return {}
    result = reader(workspace_id, cluster_ids)
    return {
        str(cluster_id): dict(value)
        for cluster_id, value in (result or {}).items()
        if isinstance(value, dict)
    }


def _latest_traffic_evidence_windows(
    db: Any,
    workspace_id: str,
    cluster_ids: set[str],
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    reader = getattr(db, "list_latest_traffic_evidence_windows", None)
    if not callable(reader):
        return []
    rows = reader(
        workspace_id,
        cluster_ids,
        since=since,
        until=until,
    )
    return [dict(row) for row in rows if isinstance(row, dict)]


async def _require_traffic_namespaces(
    db: Any,
    *,
    workspace_id: str,
    selected_cluster_ids: set[str],
    namespace_refs: set[tuple[str, str]],
    contexts: dict[str, dict[str, Any]],
) -> None:
    if not namespace_refs:
        return
    reader = getattr(db, "resolve_filter_namespaces", None)
    if not callable(reader):
        raise HTTPException(status_code=503, detail=TRAFFIC_NAMESPACE_AUTHORITY_UNAVAILABLE)
    snapshot_revision = max(
        (int(context.get("snapshot_revision") or 0) for context in contexts.values()),
        default=0,
    )
    resolved = await asyncio.to_thread(
        reader,
        workspace_id,
        selected_cluster_ids,
        snapshot_revision,
        namespace_refs,
    )
    if set(resolved or ()) != namespace_refs:
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


def _traffic_filter_fingerprint(
    *,
    clusters: tuple[str, ...],
    namespaces: tuple[tuple[str, str], ...],
    since: TrafficSince,
    protocols: tuple[str, ...],
    verdicts: tuple[str, ...],
    sort: TrafficSort,
    order: TrafficSortOrder,
    limit: int,
) -> str:
    document = {
        "clusters": clusters,
        "namespaces": namespaces,
        "since": since,
        "protocols": protocols,
        "verdicts": verdicts,
        "sort": sort,
        "order": order,
        "limit": limit,
    }
    encoded = json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _traffic_cursor_scope(
    *,
    workspace_id: str,
    user_id: str,
    authorization: str,
    fingerprint: str,
    evidence_revision: int,
) -> CursorScope:
    return CursorScope(
        workspace_id=workspace_id,
        user_id=user_id,
        authorization_revision=authorization,
        surface="traffic:flows",
        filter_fingerprint=fingerprint,
        snapshot_revision=evidence_revision,
        facet_query=None,
    )


def _traffic_cursor_offset(position: dict[str, Any]) -> int:
    if set(position) != {"offset"}:
        raise ValueError("traffic cursor position is invalid")
    value = position["offset"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("traffic cursor position is invalid")
    return value


def _cursor_codec(request: Request) -> FilterCursorCodec:
    configured = getattr(request.app.state, "inventory_filter_cursor_codec", None)
    if isinstance(configured, FilterCursorCodec):
        return configured
    try:
        return FilterCursorCodec(env(FILTER_CURSOR_SIGNING_KEY_ENV, "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=TRAFFIC_CURSOR_UNAVAILABLE) from exc


def _traffic_values(axis: str, value: str | None, allowed: set[str]) -> tuple[str, ...]:
    try:
        values = parse_filter_axis_values(
            value,
            casefold=True,
            field_name=axis,
            max_values=len(allowed),
            max_length=max(map(len, allowed)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL) from exc
    if not set(values).issubset(allowed):
        raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL)
    return values


def _traffic_since_seconds(since: TrafficSince) -> int:
    return TRAFFIC_SINCE_SECONDS[since]


def _workspace_id(current: Any) -> str:
    return str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID) or DEFAULT_WORKSPACE_ID)


def _selected(axis: str, value: str | None) -> tuple[str, ...]:
    try:
        return parse_facet_values(axis, value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL) from exc


def _namespace_refs(value: str | None) -> tuple[tuple[str, str], ...]:
    selected = _selected("namespaces", value)
    return tuple(token.rpartition("/")[::2] for token in selected)


def _require_requested_clusters(requested: Iterable[str], allowed: set[str]) -> None:
    if not set(requested).issubset(allowed):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)
