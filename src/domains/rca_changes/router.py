"""RCA incident workload의 최근 GitOps 변경 조회 API."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from domains.identity.dependencies import require_cluster_access, require_session
from domains.rca_changes.projection import trusted_pr_url
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import RecentChangeItem, RecentChangeListResponse
from packages.contracts.identity import Permission
from packages.runtime.dependencies import get_db

RECENT_CHANGES_NOT_FOUND = "incident recent changes not found"

router = APIRouter()


@router.get(
    gateway_routes.RCA_RECENT_CHANGES_PATH,
    response_model=RecentChangeListResponse,
)
async def recent_incident_changes(
    incident_id: str = Path(min_length=1, max_length=2048),
    limit: int = Query(
        default=gateway_limits.RCA_RECENT_CHANGE_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.RCA_RECENT_CHANGE_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> RecentChangeListResponse:
    workspace_id = str(getattr(current, "workspace_id", "") or "").strip()
    if not workspace_id:
        _raise_not_found()
    scopes = await asyncio.to_thread(
        db.list_incident_workload_scopes,
        workspace_id,
        incident_id,
    )
    if len(scopes) != 1:
        _raise_not_found()
    scope = scopes[0]
    cluster_id = _required_text(scope, "cluster_id")
    namespace = _required_text(scope, "namespace")
    resource_kind = _required_text(scope, "resource_kind").casefold()
    resource_name = _required_text(scope, "resource_name")
    if resource_kind == "unknown" or resource_name.casefold() == "unknown":
        _raise_not_found()
    incident_at = scope.get("incident_at")
    if incident_at is None:
        _raise_not_found()
    try:
        await asyncio.to_thread(
            require_cluster_access,
            db,
            current,
            workspace_id,
            cluster_id,
            Permission.RCA_READ.value,
            detail=RECENT_CHANGES_NOT_FOUND,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            _raise_not_found(exc)
        raise
    rows = await asyncio.to_thread(
        db.list_recent_workload_changes,
        workspace_id,
        cluster_id,
        namespace,
        resource_kind,
        resource_name,
        incident_id,
        limit=limit,
    )
    return RecentChangeListResponse(
        incident_id=incident_id,
        items=[recent_change_item(row) for row in rows],
        limit=limit,
    )


def recent_change_item(row: JsonObject) -> RecentChangeItem:
    changed_at = row["changed_at"]
    return RecentChangeItem(
        event_id=str(row["event_id"]),
        changed_at=changed_at.isoformat() if hasattr(changed_at, "isoformat") else str(changed_at),
        namespace=str(row["namespace"]),
        resource_kind=str(row["resource_kind"]),
        resource_name=str(row["resource_name"]),
        image_before=_optional_text(row.get("image_before")),
        image_after=_optional_text(row.get("image_after")),
        pr_url=_safe_pr_url(row.get("pr_url"), row.get("repo_ref")),
        commit_sha=str(row["commit_sha"]),
        repository_id=str(row["repository_id"]),
        repo_ref=str(row["repo_ref"]),
        workflow_run_id=str(row["workflow_run_id"]),
    )


def _required_text(row: JsonObject, key: str) -> str:
    value = _optional_text(row.get(key))
    if value is None:
        _raise_not_found()
    return value


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _safe_pr_url(value: object, repo_ref: object) -> str | None:
    text = _optional_text(value)
    if text is None or not trusted_pr_url(text, repo_ref):
        return None
    return text


def _raise_not_found(exc: Exception | None = None) -> None:
    error = HTTPException(status_code=404, detail=RECENT_CHANGES_NOT_FOUND)
    if exc is not None:
        raise error from exc
    raise error
