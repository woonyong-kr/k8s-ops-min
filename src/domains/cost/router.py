"""HTTP boundary for scoped Cost observation availability."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from domains.cost.node_projection import cost_node_page
from domains.cost.observation_projection import cost_overview
from domains.identity.dependencies import (
    require_session,
    resolve_allowed_application_ids,
    resolve_allowed_cluster_ids,
)
from domains.inventory_filter.cursor import CursorScope, FilterCursorCodec, authorization_revision
from domains.inventory_filter.query import (
    filter_fingerprint,
    parse_facet_values,
    parse_resource_filters,
)
from packages.config.settings import env
from packages.contracts.cost.observations import (
    CostNodePageResponse,
    CostOverviewResponse,
    CostTimeRange,
)
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.runtime.dependencies import get_db

router = APIRouter()

INVALID_SCOPE_DETAIL = "Cost scope is invalid"
SCOPE_NOT_FOUND_DETAIL = "Cost scope not found"
CURSOR_UNAVAILABLE_DETAIL = "Cost node cursor is unavailable"
MAX_CURSOR_LENGTH = 8192
MAX_NODE_PAGE_LIMIT = 200
FILTER_CURSOR_SIGNING_KEY_ENV = "FILTER_CURSOR_SIGNING_KEY"
COST_RANGE_WINDOWS = {
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


@dataclass(frozen=True)
class CostNodeScope:
    workspace_id: str
    user_id: str
    roles: tuple[str, ...]
    selected_cluster_ids: tuple[str, ...]
    namespace_refs: tuple[tuple[str, str], ...]
    allowed_application_ids: frozenset[str]
    authorization_revision: str


@router.get(gateway_routes.COST_OVERVIEW_PATH, response_model=CostOverviewResponse)
async def get_cost_overview(
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    time_range: CostTimeRange = Query(default="24h", alias="range"),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CostOverviewResponse:
    """Return only evidence that exists in the caller's inventory scope."""

    requested_clusters = _selected("clusters", clusters)
    requested_namespaces = _namespace_refs(namespaces)
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
    contexts, evidence_windows = await asyncio.gather(
        asyncio.to_thread(
            db.filter_snapshot_contexts,
            workspace_id,
            selected_clusters,
        ),
        asyncio.to_thread(
            db.list_cost_overview_evidence_windows,
            workspace_id,
            selected_clusters,
            since=datetime.now(tz=UTC) - COST_RANGE_WINDOWS[time_range],
        ),
    )
    return await asyncio.to_thread(
        cost_overview,
        workspace_id=workspace_id,
        contexts=contexts,
        selected_cluster_ids=selected_clusters,
        namespace_refs=requested_namespaces,
        time_range=time_range,
        evidence_windows=evidence_windows,
    )


@router.get(gateway_routes.COST_NODES_PATH, response_model=CostNodePageResponse)
async def get_cost_nodes(
    request: Request,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=50, ge=1, le=MAX_NODE_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CostNodePageResponse:
    """Return paginated node evidence; prices remain absent until actually observed."""

    scope = await _cost_node_scope(
        db=db,
        current=current,
        clusters=clusters,
        namespaces=namespaces,
    )
    if not scope.selected_cluster_ids:
        return cost_node_page(
            workspace_id=scope.workspace_id,
            selected_cluster_ids=(),
            namespace_refs=(),
            contexts={},
            resource_page={"items": [], "filtered_count": 0, "has_more": False},
            metric_page={"resources": [], "samples_by_cluster": {}},
            snapshot_revision=0,
            next_cursor=None,
        )
    filters = parse_resource_filters(
        clusters=",".join(scope.selected_cluster_ids),
        namespaces=None,
        applications=None,
        resource_types="node",
        health=None,
        labels=None,
        query=None,
        include_deleted=False,
    )
    codec = _cursor_codec(request)
    fingerprint = filter_fingerprint(filters)
    namespace_binding = (
        ",".join(f"{cluster_id}/{namespace}" for cluster_id, namespace in scope.namespace_refs)
        or None
    )
    if cursor is None:
        context = await asyncio.to_thread(
            db.filter_snapshot_context,
            scope.workspace_id,
            set(scope.selected_cluster_ids),
        )
        snapshot_revision = int(context.get("snapshot_revision") or 0)
        cursor_scope = _cost_cursor_scope(
            scope,
            fingerprint=fingerprint,
            snapshot_revision=snapshot_revision,
            namespace_binding=namespace_binding,
        )
        position = None
    else:
        try:
            inspected = codec.inspect(cursor)
            cursor_scope = _cost_cursor_scope(
                scope,
                fingerprint=fingerprint,
                snapshot_revision=inspected.scope.snapshot_revision,
                namespace_binding=namespace_binding,
            )
            decoded = codec.decode(cursor, expected=cursor_scope)
            position = _cost_cursor_position(decoded.position)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL) from exc
        snapshot_revision = cursor_scope.snapshot_revision
        context = await asyncio.to_thread(
            db.filter_snapshot_context,
            scope.workspace_id,
            set(scope.selected_cluster_ids),
            at_revision=snapshot_revision,
        )
        if int(context.get("snapshot_revision") or 0) != snapshot_revision:
            raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL)
    contexts = await asyncio.to_thread(
        db.filter_snapshot_contexts,
        scope.workspace_id,
        scope.selected_cluster_ids,
        at_revision=snapshot_revision,
    )
    await _require_namespaces(db, scope, snapshot_revision)
    if snapshot_revision <= 0:
        return cost_node_page(
            workspace_id=scope.workspace_id,
            selected_cluster_ids=scope.selected_cluster_ids,
            namespace_refs=scope.namespace_refs,
            contexts=contexts,
            resource_page={"items": [], "filtered_count": 0, "has_more": False},
            metric_page={"resources": [], "samples_by_cluster": {}},
            snapshot_revision=0,
            next_cursor=None,
        )
    resource_page = await asyncio.to_thread(
        db.list_filtered_resources,
        workspace_id=scope.workspace_id,
        allowed_cluster_ids=set(scope.selected_cluster_ids),
        allowed_application_ids=set(scope.allowed_application_ids),
        filters=filters,
        snapshot_revision=snapshot_revision,
        position=position,
        limit=limit,
    )
    next_cursor = _next_cost_cursor(codec, cursor_scope, resource_page.get("next_position"))
    resource_ids = tuple(
        str(resource.get("inventory_key") or "")
        for item in resource_page.get("items", ())
        if isinstance(item, dict)
        and isinstance((resource := item.get("resource")), dict)
        and resource.get("inventory_key")
    )
    metric_page = (
        await asyncio.to_thread(
            db.list_resource_metric_history,
            workspace_id=scope.workspace_id,
            allowed_cluster_ids=set(scope.selected_cluster_ids),
            allowed_application_ids=set(scope.allowed_application_ids),
            filters=filters,
            snapshot_revision=snapshot_revision,
            resource_ids=resource_ids,
            window_seconds=120,
            limit=1,
        )
        if resource_ids
        else {"resources": [], "samples_by_cluster": {}}
    )
    return cost_node_page(
        workspace_id=scope.workspace_id,
        selected_cluster_ids=scope.selected_cluster_ids,
        namespace_refs=scope.namespace_refs,
        contexts=contexts,
        resource_page=resource_page,
        metric_page=metric_page,
        snapshot_revision=snapshot_revision,
        next_cursor=next_cursor,
    )


async def _cost_node_scope(
    *, db: Any, current: Any, clusters: str | None, namespaces: str | None
) -> CostNodeScope:
    requested_clusters = _selected("clusters", clusters)
    namespace_refs = _namespace_refs(namespaces)
    workspace_id = _workspace_id(current)
    allowed_clusters, allowed_applications = await asyncio.gather(
        asyncio.to_thread(
            resolve_allowed_cluster_ids,
            db,
            current,
            workspace_id,
            Permission.INVENTORY_READ.value,
        ),
        asyncio.to_thread(
            resolve_allowed_application_ids,
            db,
            current,
            workspace_id,
            Permission.INVENTORY_READ.value,
        ),
    )
    requested_scope_clusters = set(requested_clusters) | {
        cluster_id for cluster_id, _namespace in namespace_refs
    }
    _require_requested_clusters(requested_scope_clusters, allowed_clusters)
    selected_cluster_ids = tuple(sorted(requested_scope_clusters or allowed_clusters))
    roles = tuple(getattr(current, "roles", ()) or ())
    user_id = str(getattr(current, "user_id", ""))
    return CostNodeScope(
        workspace_id=workspace_id,
        user_id=user_id,
        roles=roles,
        selected_cluster_ids=selected_cluster_ids,
        namespace_refs=namespace_refs,
        allowed_application_ids=frozenset(allowed_applications),
        authorization_revision=authorization_revision(
            user_id=user_id,
            workspace_id=workspace_id,
            roles=roles,
            allowed_cluster_ids=allowed_clusters,
            allowed_application_ids=allowed_applications,
        ),
    )


async def _require_namespaces(db: Any, scope: CostNodeScope, snapshot_revision: int) -> None:
    requested = set(scope.namespace_refs)
    if not requested:
        return
    resolved = await asyncio.to_thread(
        db.resolve_filter_namespaces,
        scope.workspace_id,
        set(scope.selected_cluster_ids),
        snapshot_revision,
        requested,
    )
    if resolved != requested:
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


def _cost_cursor_scope(
    scope: CostNodeScope,
    *,
    fingerprint: str,
    snapshot_revision: int,
    namespace_binding: str | None,
) -> CursorScope:
    return CursorScope(
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
        authorization_revision=scope.authorization_revision,
        surface="cost:nodes",
        filter_fingerprint=fingerprint,
        snapshot_revision=snapshot_revision,
        facet_query=namespace_binding,
    )


def _cursor_codec(request: Request) -> FilterCursorCodec:
    configured = getattr(request.app.state, "inventory_filter_cursor_codec", None)
    if isinstance(configured, FilterCursorCodec):
        return configured
    try:
        return FilterCursorCodec(env(FILTER_CURSOR_SIGNING_KEY_ENV, "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=CURSOR_UNAVAILABLE_DETAIL) from exc


def _cost_cursor_position(position: dict[str, Any]) -> dict[str, Any]:
    required = {"cluster_id", "namespace", "resource_type", "kind", "name", "inventory_key"}
    if set(position) != required or any(not isinstance(position[key], str) for key in required):
        raise ValueError("cost node cursor position is invalid")
    return {key: str(position[key]) for key in required}


def _next_cost_cursor(
    codec: FilterCursorCodec,
    scope: CursorScope,
    position: object,
) -> str | None:
    if not isinstance(position, dict):
        return None
    try:
        return codec.encode(scope, position=_cost_cursor_position(position))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL) from exc


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
