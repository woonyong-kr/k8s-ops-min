"""Authenticated, read-only safe Compare API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from domains.compare.projection import (
    CompareIdentityUnavailable,
    CompareNotFound,
    CompareUnavailable,
    CompareUnsupported,
    compare_candidates,
    compare_resource_pair,
    comparison_descriptors,
    parse_compare_target,
)
from domains.identity.dependencies import require_cluster_access, require_session
from packages.contracts.comparable_manifest import (
    CompareCandidateListResponse,
    CompareDescriptorListResponse,
    CompareResourcePairResponse,
)
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.runtime.dependencies import get_db

router = APIRouter()


@router.get(
    gateway_routes.COMPARE_DESCRIPTORS_PATH,
    response_model=CompareDescriptorListResponse,
)
async def list_compare_descriptors(
    _current: Any = Depends(require_session),
) -> CompareDescriptorListResponse:
    """Expose only server-owned safe descriptor metadata for route launchers."""
    return CompareDescriptorListResponse(descriptors=comparison_descriptors())


@router.get(
    gateway_routes.COMPARE_CANDIDATES_PATH,
    response_model=CompareCandidateListResponse,
)
async def list_compare_candidates(
    cluster_id: str = Query(min_length=1, max_length=512),
    kind: str = Query(min_length=1, max_length=120),
    api_group: str = Query(default="", max_length=253, alias="apiGroup"),
    api_version: str | None = Query(default=None, max_length=63, alias="apiVersion"),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CompareCandidateListResponse:
    workspace_id = authorized_workspace(db, current, cluster_id)
    try:
        result = compare_candidates(
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            route_kind=kind,
            api_group=api_group,
            api_version=api_version,
        )
    except (ValueError, CompareUnsupported) as exc:
        raise HTTPException(status_code=422, detail="safe comparison is unavailable") from exc
    except CompareUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="comparison observation is unavailable"
        ) from exc
    return CompareCandidateListResponse(result=result)


@router.get(
    gateway_routes.COMPARE_RESOURCES_PATH,
    response_model=CompareResourcePairResponse,
)
async def get_compare_resource_pair(
    cluster_id: str = Query(min_length=1, max_length=512),
    kind: str = Query(min_length=1, max_length=120),
    a: str = Query(min_length=1, max_length=317),
    b: str = Query(min_length=1, max_length=317),
    api_group: str = Query(default="", max_length=253, alias="apiGroup"),
    api_version: str | None = Query(default=None, max_length=63, alias="apiVersion"),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CompareResourcePairResponse:
    workspace_id = authorized_workspace(db, current, cluster_id)
    try:
        comparison = compare_resource_pair(
            db,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            route_kind=kind,
            api_group=api_group,
            api_version=api_version,
            a=parse_compare_target(a),
            b=parse_compare_target(b),
        )
    except (ValueError, CompareUnsupported) as exc:
        raise HTTPException(status_code=422, detail="safe comparison is unavailable") from exc
    except CompareNotFound as exc:
        raise HTTPException(status_code=404, detail="comparison observation not found") from exc
    except CompareIdentityUnavailable as exc:
        raise HTTPException(status_code=409, detail="comparison identity is incomplete") from exc
    except CompareUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="comparison observation is unavailable"
        ) from exc
    return CompareResourcePairResponse(comparison=comparison)


def authorized_workspace(db: Any, current: Any, cluster_id: str) -> str:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )
    return workspace_id
