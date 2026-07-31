"""Authenticated Workload Detail route backed only by safe inventory projection."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from domains.identity.dependencies import require_cluster_access, require_session
from domains.target.connectivity import (
    AGENT_STATUS_NEVER_CONNECTED,
    AGENT_STATUS_ONLINE,
    AGENT_STATUS_STALE,
)
from domains.workload_detail.projection import (
    WorkloadDetailIdentityUnavailable,
    WorkloadDetailNotFound,
    WorkloadDetailUnavailable,
    cluster_liveness,
    workload_detail_projection,
)
from domains.workload_detail.rightsizing_projection import rightsizing_scan_result
from packages.config.refresh_policies import integral_refresh_after_seconds
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.parity import ClusterScope
from packages.contracts.rightsizing import (
    MAX_RIGHTSIZING_WORKLOADS,
    RightsizingScanResponse,
)
from packages.contracts.workload_detail import WorkloadDetailResponse
from packages.runtime.dependencies import get_db

KUBERNETES_NAME_PATTERN = r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$"
KUBERNETES_NAMESPACE_PATTERN = r"^(?:_|[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)$"
KUBERNETES_KIND_PATTERN = r"^[A-Za-z][A-Za-z0-9.-]*$"

router = APIRouter()


@router.get(
    gateway_routes.WORKLOAD_DETAIL_PATH,
    response_model=WorkloadDetailResponse,
)
async def get_workload_detail(
    kind: str = Path(min_length=1, max_length=120, pattern=KUBERNETES_KIND_PATTERN),
    namespace: str = Path(min_length=1, max_length=63, pattern=KUBERNETES_NAMESPACE_PATTERN),
    name: str = Path(min_length=1, max_length=253, pattern=KUBERNETES_NAME_PATTERN),
    cluster_id: str = Query(min_length=1, max_length=512),
    api_group: str = Query(default="", max_length=253, alias="apiGroup"),
    api_version: str = Query(min_length=1, max_length=63, alias="apiVersion"),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> WorkloadDetailResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )
    try:
        detail = workload_detail_projection(
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            api_group=api_group,
            api_version=api_version,
            kind=kind,
            namespace=None if namespace == "_" else namespace,
            name=name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="workload API identity is invalid") from exc
    except WorkloadDetailNotFound as exc:
        raise HTTPException(status_code=404, detail="workload observation not found") from exc
    except WorkloadDetailIdentityUnavailable as exc:
        raise HTTPException(status_code=409, detail="workload identity is incomplete") from exc
    except WorkloadDetailUnavailable as exc:
        raise HTTPException(status_code=503, detail="workload observation is unavailable") from exc
    return WorkloadDetailResponse(detail=detail)


@router.get(
    gateway_routes.RIGHTSIZING_SCAN_PATH,
    response_model=RightsizingScanResponse,
)
async def get_rightsizing_scan(
    cluster_id: str = Query(min_length=1, max_length=512),
    namespaces: str | None = Query(default=None, max_length=25_400),
    limit: int = Query(default=100, ge=1, le=MAX_RIGHTSIZING_WORKLOADS),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> RightsizingScanResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )
    namespace_scope = _rightsizing_namespaces(namespaces)
    connection, _reason = cluster_liveness(db, workspace_id, cluster_id)
    freshness = {
        AGENT_STATUS_ONLINE: "live",
        AGENT_STATUS_STALE: "stale",
        AGENT_STATUS_NEVER_CONNECTED: "disconnected",
    }.get(connection, "partial")
    return RightsizingScanResponse(
        scope=ClusterScope(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            namespaces=namespace_scope,
            freshness=freshness,
        ),
        namespace_scope=namespace_scope,
        result=await asyncio.to_thread(
            rightsizing_scan_result,
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            namespaces=namespace_scope,
            limit=limit,
        ),
        refresh_after_seconds=integral_refresh_after_seconds("metrics_rightsizing"),
    )


def _rightsizing_namespaces(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    tokens = tuple(item.strip() for item in value.split(","))
    if not tokens or len(tokens) > 100:
        raise HTTPException(status_code=422, detail="rightsizing namespace scope is invalid")
    if any(
        not token
        or len(token) > 63
        or token == "_"
        or re.fullmatch(KUBERNETES_NAMESPACE_PATTERN, token) is None
        for token in tokens
    ):
        raise HTTPException(status_code=422, detail="rightsizing namespace scope is invalid")
    return tuple(sorted(set(tokens)))
