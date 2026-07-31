"""HTTP adapter for the bounded W4 activity trend."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from domains.identity.dependencies import require_session
from domains.timeline.access import resolve_authorized_timeline_scope
from packages.contracts.gateway import params as gateway_params
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import ActivityOverviewResponse
from packages.runtime.dependencies import get_db

ACTIVITY_MIN_BUCKET_MS = 60_000
ACTIVITY_MAX_BUCKET_MS = 30 * 24 * 60 * 60 * 1_000
ACTIVITY_MAX_RANGE_MS = 30 * 24 * 60 * 60 * 1_000
ACTIVITY_MAX_BUCKETS = 366
ACTIVITY_MAX_EPOCH_MS = 253_402_300_799_000
INVALID_ACTIVITY_WINDOW = "activity overview request is invalid"
ACTIVITY_UNAVAILABLE = "activity overview is unavailable"

router = APIRouter()


@router.get(
    gateway_routes.ACTIVITY_OVERVIEW_PATH,
    response_model=ActivityOverviewResponse,
)
async def read_activity_overview(
    response: Response,
    from_ms: int = Query(
        alias=gateway_params.TIME_RANGE_FROM_QUERY,
        ge=0,
        le=ACTIVITY_MAX_EPOCH_MS,
    ),
    to_ms: int = Query(
        alias=gateway_params.TIME_RANGE_TO_QUERY,
        ge=1,
        le=ACTIVITY_MAX_EPOCH_MS,
    ),
    bucket_ms: int = Query(
        alias=gateway_params.CHANGE_BUCKET_QUERY,
        ge=ACTIVITY_MIN_BUCKET_MS,
        le=ACTIVITY_MAX_BUCKET_MS,
    ),
    cluster_ids: list[str] = Query(default=[], alias=gateway_params.CLUSTERS_QUERY),
    namespaces: list[str] = Query(default=[], alias=gateway_params.NAMESPACES_QUERY),
    applications: list[str] = Query(default=[], alias=gateway_params.APPLICATIONS_QUERY),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ActivityOverviewResponse:
    _validate_activity_window(from_ms=from_ms, to_ms=to_ms, bucket_ms=bucket_ms)
    authorized = await resolve_authorized_timeline_scope(db, current)
    alert_clusters = set(authorized.cluster_ids)
    incident_clusters = set(authorized.incident_cluster_ids)
    deployment_applications = set(authorized.deployment_application_ids)
    requested_clusters: set[str] = set()
    if cluster_ids:
        requested_clusters = alert_clusters.intersection(cluster_ids)
        alert_clusters.intersection_update(cluster_ids)
        incident_clusters.intersection_update(cluster_ids)
    if applications:
        deployment_applications.intersection_update(applications)
    reader = getattr(db, "activity_overview", None)
    if not callable(reader):
        raise HTTPException(status_code=503, detail=ACTIVITY_UNAVAILABLE)
    buckets = await asyncio.to_thread(
        reader,
        workspace_id=authorized.workspace_id,
        deployment_application_ids=deployment_applications,
        alert_cluster_ids=alert_clusters,
        incident_cluster_ids=incident_clusters,
        requested_cluster_ids=requested_clusters,
        requested_namespaces=set(namespaces),
        from_ms=from_ms,
        to_ms=to_ms,
        bucket_ms=bucket_ms,
    )
    response.headers["Cache-Control"] = "no-store"
    return ActivityOverviewResponse(
        from_ms=from_ms,
        to_ms=to_ms,
        bucket_ms=bucket_ms,
        buckets=buckets,
    )


def _validate_activity_window(*, from_ms: int, to_ms: int, bucket_ms: int) -> None:
    width = to_ms - from_ms
    if (
        width <= 0
        or width > ACTIVITY_MAX_RANGE_MS
        or bucket_ms > width
        or (width + bucket_ms - 1) // bucket_ms > ACTIVITY_MAX_BUCKETS
    ):
        raise HTTPException(status_code=422, detail=INVALID_ACTIVITY_WINDOW)
