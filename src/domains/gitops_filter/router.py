"""Tenant-safe GitOps change list and server-side facet endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from domains.gitops_filter.query import (
    GitOpsFacetAxis,
    GitOpsFilters,
    gitops_filter_fingerprint,
    parse_gitops_filters,
    selected_facet_values,
)
from domains.identity.dependencies import (
    require_session,
    resolve_allowed_application_ids,
    resolve_allowed_cluster_ids,
)
from domains.inventory_filter.cursor import CursorScope, FilterCursorCodec, authorization_revision
from packages.config.settings import env
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import params as gateway_params
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import (
    FilterResultCounts,
    FilterSnapshotMeta,
    GitOpsFilterCapability,
    GitOpsFilterFacetPageResponse,
    GitOpsFilterItem,
    GitOpsFilterResultsResponse,
)
from packages.contracts.identity import Permission
from packages.runtime.dependencies import get_db

DEFAULT_PAGE_LIMIT = gateway_limits.FILTER_FACET_DEFAULT_LIMIT
MAX_PAGE_LIMIT = gateway_limits.FILTER_FACET_MAX_LIMIT
MAX_CURSOR_LENGTH = gateway_limits.FILTER_CURSOR_MAX_LENGTH
MAX_FILTER_SEARCH_LENGTH = gateway_limits.FILTER_SEARCH_MAX_LENGTH
FILTER_CURSOR_SIGNING_KEY_ENV = "FILTER_CURSOR_SIGNING_KEY"
INVALID_REQUEST_DETAIL = "GitOps filter request is invalid"
SCOPE_NOT_FOUND_DETAIL = "GitOps filter scope not found"
CURSOR_UNAVAILABLE_DETAIL = "GitOps filter cursor is unavailable"
PAGINATION_UNAVAILABLE_DETAIL = "GitOps filter pagination is unavailable"
MUTABLE_PROJECTION_REASON = "mutable_gitops_projection"

router = APIRouter()


@dataclass(frozen=True)
class AuthorizedGitOpsScope:
    workspace_id: str
    user_id: str
    roles: tuple[str, ...]
    cluster_ids: frozenset[str]
    application_ids: frozenset[str]
    authorization_revision: str


@router.get(
    gateway_routes.GITOPS_FILTER_RESULTS_PATH,
    response_model=GitOpsFilterResultsResponse,
)
async def list_filtered_gitops_changes(
    request: Request,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    gitops_environment: str | None = Query(
        default=None,
        alias=gateway_params.GITOPS_ENVIRONMENT_QUERY,
    ),
    gitops_approval: str | None = Query(
        default=None,
        alias=gateway_params.GITOPS_APPROVAL_QUERY,
    ),
    gitops_change_type: str | None = Query(
        default=None,
        alias=gateway_params.GITOPS_CHANGE_TYPE_QUERY,
    ),
    gitops_q: str | None = Query(default=None, alias=gateway_params.GITOPS_SEARCH_QUERY),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> GitOpsFilterResultsResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        environments=gitops_environment,
        approvals=gitops_approval,
        change_types=gitops_change_type,
        labels=labels,
        query=gitops_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = gitops_filter_fingerprint(filters)
    if not authorized.cluster_ids or not authorized.application_ids:
        return _empty_results(authorized, fingerprint, filters)
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface="gitops:list",
        fingerprint=fingerprint,
        facet_query=None,
        position_keys=("updated_at", "change_id"),
    )
    _reject_unpinned_cursor(position)
    result = await asyncio.to_thread(
        db.list_filtered_gitops_changes,
        workspace_id=authorized.workspace_id,
        allowed_cluster_ids=set(authorized.cluster_ids),
        allowed_application_ids=set(authorized.application_ids),
        filters=filters,
        position=position,
        limit=limit,
    )
    return _results_response(
        result,
        codec=codec,
        scope=scope,
        authorized=authorized,
        fingerprint=fingerprint,
        filters=filters,
    )


@router.get(
    gateway_routes.GITOPS_FILTER_FACETS_PATH,
    response_model=GitOpsFilterFacetPageResponse,
)
async def list_gitops_filter_facets(
    request: Request,
    axis: GitOpsFacetAxis,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    gitops_environment: str | None = Query(
        default=None,
        alias=gateway_params.GITOPS_ENVIRONMENT_QUERY,
    ),
    gitops_approval: str | None = Query(
        default=None,
        alias=gateway_params.GITOPS_APPROVAL_QUERY,
    ),
    gitops_change_type: str | None = Query(
        default=None,
        alias=gateway_params.GITOPS_CHANGE_TYPE_QUERY,
    ),
    gitops_q: str | None = Query(default=None, alias=gateway_params.GITOPS_SEARCH_QUERY),
    facet_q: str | None = Query(default=None, max_length=MAX_FILTER_SEARCH_LENGTH),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> GitOpsFilterFacetPageResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        environments=gitops_environment,
        approvals=gitops_approval,
        change_types=gitops_change_type,
        labels=labels,
        query=gitops_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = gitops_filter_fingerprint(filters)
    if not authorized.cluster_ids or not authorized.application_ids:
        return _empty_facets(axis, authorized, fingerprint, filters)
    normalized_query = (facet_q or "").strip().casefold() or None
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface=f"gitops:facet:{axis}",
        fingerprint=fingerprint,
        facet_query=normalized_query,
        position_keys=("value",),
    )
    _reject_unpinned_cursor(position)
    result = await asyncio.to_thread(
        db.list_gitops_filter_facets,
        workspace_id=authorized.workspace_id,
        allowed_cluster_ids=set(authorized.cluster_ids),
        allowed_application_ids=set(authorized.application_ids),
        filters=filters,
        axis=axis,
        facet_query=normalized_query,
        position=position,
        limit=limit,
    )
    next_position = result.get("next_position")
    _reject_unpinned_result_page(result, next_position)
    capabilities = _capabilities(result)
    items = _facet_items(result, axis)
    return GitOpsFilterFacetPageResponse(
        surface="gitops",
        axis=axis,
        items=items,
        selected_resolutions=_selected_resolutions(filters, axis, items),
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        capabilities=capabilities,
    )


async def _authorized_scope(db: Any, current: Any) -> AuthorizedGitOpsScope:
    workspace_id = str(getattr(current, "workspace_id", "") or "").strip()
    user_id = str(getattr(current, "user_id", "") or "").strip()
    roles = tuple(str(role) for role in (getattr(current, "roles", ()) or ()))
    if not workspace_id or not user_id:
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)

    def resolve() -> tuple[set[str], set[str]]:
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

    cluster_ids, application_ids = await asyncio.to_thread(resolve)
    revision = authorization_revision(
        user_id=user_id,
        workspace_id=workspace_id,
        roles=roles,
        allowed_cluster_ids=cluster_ids,
        allowed_application_ids=application_ids,
    )
    return AuthorizedGitOpsScope(
        workspace_id=workspace_id,
        user_id=user_id,
        roles=roles,
        cluster_ids=frozenset(cluster_ids),
        application_ids=frozenset(application_ids),
        authorization_revision=revision,
    )


def _require_requested_scope(authorized: AuthorizedGitOpsScope, filters: GitOpsFilters) -> None:
    requested_clusters = set(filters.clusters) | {
        cluster_id for cluster_id, _namespace in filters.namespaces
    }
    if not requested_clusters.issubset(authorized.cluster_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)
    if not set(filters.applications).issubset(authorized.application_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


def _parse_filters(**kwargs: Any) -> GitOpsFilters:
    try:
        return parse_gitops_filters(**kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_REQUEST_DETAIL) from exc


def _cursor_codec(request: Request) -> FilterCursorCodec:
    configured = getattr(request.app.state, "gitops_filter_cursor_codec", None)
    if isinstance(configured, FilterCursorCodec):
        return configured
    try:
        return FilterCursorCodec(env(FILTER_CURSOR_SIGNING_KEY_ENV, "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=CURSOR_UNAVAILABLE_DETAIL) from exc


def _page_scope(
    codec: FilterCursorCodec,
    *,
    cursor: str | None,
    authorized: AuthorizedGitOpsScope,
    surface: str,
    fingerprint: str,
    facet_query: str | None,
    position_keys: tuple[str, ...],
) -> tuple[CursorScope, dict[str, Any] | None]:
    scope = CursorScope(
        workspace_id=authorized.workspace_id,
        user_id=authorized.user_id,
        authorization_revision=authorized.authorization_revision,
        surface=surface,
        filter_fingerprint=fingerprint,
        snapshot_revision=0,
        facet_query=facet_query,
    )
    if cursor is None:
        return scope, None
    try:
        decoded = codec.decode(cursor, expected=scope)
        position = decoded.position
        if set(position) != set(position_keys) or any(
            not isinstance(position.get(key), str) or not position[key] for key in position_keys
        ):
            raise ValueError("cursor position is invalid")
        return scope, {key: str(position[key]) for key in position_keys}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_REQUEST_DETAIL) from exc


def _next_cursor(codec: FilterCursorCodec, scope: CursorScope, position: object) -> str | None:
    if not isinstance(position, Mapping):
        return None
    try:
        return codec.encode(scope, position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_REQUEST_DETAIL) from exc


def _reject_unpinned_cursor(position: Mapping[str, Any] | None) -> None:
    if position is not None:
        raise HTTPException(status_code=503, detail=PAGINATION_UNAVAILABLE_DETAIL)


def _reject_unpinned_result_page(result: Mapping[str, Any], next_position: object) -> None:
    if isinstance(next_position, Mapping) or bool(result.get("has_more")):
        raise HTTPException(status_code=503, detail=PAGINATION_UNAVAILABLE_DETAIL)


def _results_response(
    result: Mapping[str, Any],
    *,
    codec: FilterCursorCodec,
    scope: CursorScope,
    authorized: AuthorizedGitOpsScope,
    fingerprint: str,
    filters: GitOpsFilters,
) -> GitOpsFilterResultsResponse:
    next_position = result.get("next_position")
    _reject_unpinned_result_page(result, next_position)
    return GitOpsFilterResultsResponse(
        items=[_safe_item(row) for row in list(result.get("items") or [])],
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        facets=_facet_items(result, None),
        capabilities=_capabilities(result),
        selected_labels=_selected_labels(filters),
    )


def _safe_item(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise HTTPException(status_code=500, detail="GitOps filter projection is invalid")
    return {key: row.get(key) for key in GitOpsFilterItem.model_fields}


def _counts(result: Mapping[str, Any]) -> FilterResultCounts:
    source = result.get("counts")
    values = source if isinstance(source, Mapping) else result
    filtered = values.get("filtered_count")
    unfiltered = values.get("unfiltered_count")
    filtered_available = isinstance(filtered, int) and not isinstance(filtered, bool)
    unfiltered_available = isinstance(unfiltered, int) and not isinstance(unfiltered, bool)
    return FilterResultCounts(
        filtered_count=int(filtered) if filtered_available else None,
        unfiltered_count=int(unfiltered) if unfiltered_available else None,
        filtered_count_completeness=str(
            values.get("filtered_count_completeness")
            or ("partial" if filtered_available else "unavailable")
        ),
        unfiltered_count_completeness=str(
            values.get("unfiltered_count_completeness")
            or ("partial" if unfiltered_available else "unavailable")
        ),
    )


def _snapshot(
    authorized: AuthorizedGitOpsScope,
    fingerprint: str,
    result: Mapping[str, Any],
) -> FilterSnapshotMeta:
    return FilterSnapshotMeta(
        snapshot_revision=0,
        authorization_revision=authorized.authorization_revision,
        filter_fingerprint=fingerprint,
        observed_at=str(result["observed_at"]) if result.get("observed_at") else None,
        stale=False,
        partial_reason_codes=sorted(
            {
                str(reason)
                for reason in (result.get("partial_reason_codes") or [MUTABLE_PROJECTION_REASON])
                if str(reason)
            }
        ),
    )


def _facet_items(result: Mapping[str, Any], axis: GitOpsFacetAxis | None) -> list[dict[str, Any]]:
    source = result.get("items") if axis is not None else result.get("facets")
    if not isinstance(source, list):
        return []
    items: list[dict[str, Any]] = []
    for row in source:
        if not isinstance(row, Mapping):
            continue
        item_axis = str(row.get("axis") or axis or "")
        if not item_axis:
            continue
        items.append(
            {
                "axis": item_axis,
                "value": row.get("value"),
                "label": row.get("label"),
                "match_count": row.get("match_count"),
                "count_completeness": row.get("count_completeness", "partial"),
                "availability": row.get("availability", "partial"),
            }
        )
    return items


def _capabilities(result: Mapping[str, Any]) -> list[GitOpsFilterCapability]:
    source = result.get("capabilities")
    if isinstance(source, list):
        return [GitOpsFilterCapability.model_validate(item) for item in source]
    return [
        GitOpsFilterCapability(
            axis=axis,
            availability="unavailable" if axis in {"change_type", "labels"} else "partial",
            reason_code=(
                "gitops_change_type_projection_unavailable"
                if axis == "change_type"
                else "gitops_label_projection_unavailable"
                if axis == "labels"
                else MUTABLE_PROJECTION_REASON
            ),
            source_semantics=source_semantics,
        )
        for axis, source_semantics in (
            ("clusters", "authorized_workflow_run"),
            ("namespaces", "deployment_binding"),
            ("applications", "workflow_application"),
            ("environment", "workflow_environment"),
            ("approval", "latest_workflow_approval"),
            ("change_type", "workflow_diff_projection"),
            ("labels", "desired_manifest_resource_snapshot"),
        )
    ]


def _selected_resolutions(
    filters: GitOpsFilters,
    axis: GitOpsFacetAxis,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values = {str(item["value"]): item for item in items}
    response_axis = {
        "clusters": "cluster",
        "namespaces": "namespace",
        "applications": "application",
    }.get(axis, axis)
    return [
        {
            "axis": response_axis,
            "value": value,
            "status": (
                "unavailable"
                if axis == "change_type"
                else "resolved"
                if value in values
                else "zero"
            ),
            "display_label": str(values[value]["label"]) if value in values else value,
        }
        for value in selected_facet_values(filters, axis)
    ]


def _selected_labels(filters: GitOpsFilters) -> list[dict[str, str]]:
    return [
        {"key": key, "value": value, "selector": f"{key}={value}", "status": "unavailable"}
        for key, value in filters.labels
    ]


def _empty_results(
    authorized: AuthorizedGitOpsScope,
    fingerprint: str,
    filters: GitOpsFilters,
) -> GitOpsFilterResultsResponse:
    return GitOpsFilterResultsResponse(
        items=[],
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        facets=[],
        capabilities=_capabilities({}),
        selected_labels=_selected_labels(filters),
    )


def _empty_facets(
    axis: GitOpsFacetAxis,
    authorized: AuthorizedGitOpsScope,
    fingerprint: str,
    filters: GitOpsFilters,
) -> GitOpsFilterFacetPageResponse:
    return GitOpsFilterFacetPageResponse(
        surface="gitops",
        axis=axis,
        items=[],
        selected_resolutions=_selected_resolutions(filters, axis, []),
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        capabilities=_capabilities({}),
    )


def _exact_empty_counts() -> FilterResultCounts:
    return FilterResultCounts(
        filtered_count=0,
        unfiltered_count=0,
        filtered_count_completeness="exact",
        unfiltered_count_completeness="exact",
    )


def _empty_snapshot(
    authorized: AuthorizedGitOpsScope,
    fingerprint: str,
) -> FilterSnapshotMeta:
    return FilterSnapshotMeta(
        snapshot_revision=0,
        authorization_revision=authorized.authorization_revision,
        filter_fingerprint=fingerprint,
        observed_at=None,
        stale=False,
        partial_reason_codes=[],
    )
