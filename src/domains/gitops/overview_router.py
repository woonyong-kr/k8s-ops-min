"""Authorized browser route for the canonical mixed GitOps fleet overview."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from domains.gitops.overview_projection import project_gitops_overview
from domains.gitops.overview_query import parse_gitops_overview_filters
from domains.identity.dependencies import (
    require_session,
    resolve_allowed_application_ids,
    resolve_allowed_cluster_ids,
)
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gitops.overview import GitOpsOverviewResponse
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.runtime.dependencies import get_db

router = APIRouter()
SCOPE_NOT_FOUND_DETAIL = "GitOps overview scope not found"
INVALID_REQUEST_DETAIL = "GitOps overview request is invalid"


@router.get(
    gateway_routes.GITOPS_OVERVIEW_PATH,
    response_model=GitOpsOverviewResponse,
)
async def list_gitops_overview(
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    providers: str | None = Query(default=None),
    kinds: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> GitOpsOverviewResponse:
    try:
        filters = parse_gitops_overview_filters(
            clusters=clusters,
            namespaces=namespaces,
            applications=applications,
            providers=providers,
            kinds=kinds,
            labels=labels,
            query=q,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=INVALID_REQUEST_DETAIL) from error

    workspace_id = str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID) or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)

    def resolve_scope() -> tuple[set[str], set[str]]:
        return (
            resolve_allowed_cluster_ids(
                db,
                current,
                workspace_id,
                Permission.INVENTORY_READ.value,
            ),
            resolve_allowed_application_ids(
                db,
                current,
                workspace_id,
                Permission.APPLICATION_READ.value,
            ),
        )

    allowed_cluster_ids, allowed_application_ids = await asyncio.to_thread(resolve_scope)
    requested_cluster_ids = set(filters.clusters) | {
        cluster_id for cluster_id, _namespace in filters.namespaces
    }
    if not requested_cluster_ids.issubset(allowed_cluster_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)
    if not set(filters.applications).issubset(allowed_application_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)

    selected_cluster_ids = (
        requested_cluster_ids if requested_cluster_ids else set(allowed_cluster_ids)
    )
    if not selected_cluster_ids:
        return project_gitops_overview(
            workspace_id=workspace_id,
            registered_rows=[],
            inventory_rows=[],
            snapshot_contexts={},
            has_more=False,
        )
    contexts = await asyncio.to_thread(
        db.filter_snapshot_contexts,
        workspace_id,
        selected_cluster_ids,
    )
    snapshot_revision = max(
        (int(context.get("snapshot_revision") or 0) for context in contexts.values()),
        default=0,
    )
    result = await asyncio.to_thread(
        db.list_gitops_overview,
        workspace_id=workspace_id,
        allowed_cluster_ids=selected_cluster_ids,
        allowed_application_ids=set(allowed_application_ids),
        filters=filters,
        snapshot_revision=snapshot_revision,
        limit=limit,
    )
    return project_gitops_overview(
        workspace_id=workspace_id,
        registered_rows=result.get("registered_rows") or [],
        inventory_rows=result.get("inventory_rows") or [],
        snapshot_contexts=contexts,
        has_more=bool(result.get("has_more")),
    )
