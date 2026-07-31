"""Tenant-safe Applications list and server-side facet endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from domains.application_filter.query import (
    ApplicationFacetAxis,
    ApplicationFilters,
    application_filter_fingerprint,
    parse_application_filters,
    selected_facet_values,
)
from domains.identity.dependencies import (
    require_session,
    resolve_allowed_application_ids,
    resolve_allowed_cluster_ids,
)
from domains.inventory_filter.cursor import (
    CursorScope,
    FilterCursorCodec,
    authorization_revision,
)
from packages.config.settings import env
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import params as gateway_params
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import (
    ApplicationFilterCapability,
    ApplicationFilterFacetPageResponse,
    ApplicationFilterItem,
    ApplicationFilterResultsResponse,
    ApplicationLabelFacetPageResponse,
    FilterResultCounts,
    FilterSnapshotMeta,
)
from packages.contracts.identity import Permission
from packages.runtime.dependencies import get_db

DEFAULT_PAGE_LIMIT = gateway_limits.FILTER_FACET_DEFAULT_LIMIT
MAX_PAGE_LIMIT = gateway_limits.FILTER_FACET_MAX_LIMIT
MAX_CURSOR_LENGTH = gateway_limits.FILTER_CURSOR_MAX_LENGTH
MAX_FILTER_SEARCH_LENGTH = gateway_limits.FILTER_SEARCH_MAX_LENGTH
FILTER_CURSOR_SIGNING_KEY_ENV = "FILTER_CURSOR_SIGNING_KEY"
INVALID_REQUEST_DETAIL = "application filter request is invalid"
SCOPE_NOT_FOUND_DETAIL = "application filter scope not found"
CURSOR_UNAVAILABLE_DETAIL = "application filter cursor is unavailable"
PAGINATION_UNAVAILABLE_DETAIL = "application filter pagination is unavailable"
MUTABLE_PROJECTION_REASON = "mutable_application_projection"

router = APIRouter()


@dataclass(frozen=True)
class AuthorizedApplicationScope:
    workspace_id: str
    user_id: str
    roles: tuple[str, ...]
    cluster_ids: frozenset[str]
    application_ids: frozenset[str]
    authorization_revision: str


@router.get(
    gateway_routes.APPLICATION_FILTER_RESULTS_PATH,
    response_model=ApplicationFilterResultsResponse,
)
async def list_filtered_applications(
    request: Request,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    applications_environment: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_ENVIRONMENT_QUERY,
    ),
    applications_status: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_STATUS_QUERY,
    ),
    applications_pending_promotion: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_PENDING_PROMOTION_QUERY,
    ),
    applications_q: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_SEARCH_QUERY,
    ),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ApplicationFilterResultsResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        environments=applications_environment,
        statuses=applications_status,
        pending_promotion=applications_pending_promotion,
        labels=labels,
        query=applications_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = application_filter_fingerprint(filters)
    if not authorized.application_ids:
        return _empty_results(authorized, fingerprint, filters)
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface="applications:list",
        fingerprint=fingerprint,
        facet_query=None,
        position_keys=("name", "application_id"),
    )
    _reject_unpinned_cursor(position)
    result = await asyncio.to_thread(
        db.list_filtered_applications,
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
    gateway_routes.APPLICATION_FILTER_FACETS_PATH,
    response_model=ApplicationFilterFacetPageResponse,
)
async def list_application_filter_facets(
    request: Request,
    axis: ApplicationFacetAxis,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    applications_environment: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_ENVIRONMENT_QUERY,
    ),
    applications_status: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_STATUS_QUERY,
    ),
    applications_pending_promotion: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_PENDING_PROMOTION_QUERY,
    ),
    applications_q: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_SEARCH_QUERY,
    ),
    facet_q: str | None = Query(default=None, max_length=MAX_FILTER_SEARCH_LENGTH),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ApplicationFilterFacetPageResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        environments=applications_environment,
        statuses=applications_status,
        pending_promotion=applications_pending_promotion,
        labels=labels,
        query=applications_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = application_filter_fingerprint(filters)
    normalized_query = (facet_q or "").strip().casefold() or None
    if not authorized.application_ids:
        return _empty_facet_response(axis, authorized, fingerprint, filters)
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface=f"applications:facet:{axis}",
        fingerprint=fingerprint,
        facet_query=normalized_query,
        position_keys=("value",),
    )
    _reject_unpinned_cursor(position)
    result = await asyncio.to_thread(
        db.list_application_filter_facets,
        workspace_id=authorized.workspace_id,
        allowed_cluster_ids=set(authorized.cluster_ids),
        allowed_application_ids=set(authorized.application_ids),
        filters=filters,
        axis=axis,
        facet_query=normalized_query,
        position=position,
        limit=limit,
    )
    capabilities = _capabilities(result)
    items = _facet_items(result, requested_axis=axis)
    next_position = result.get("next_position")
    _reject_unpinned_result_page(result, next_position)
    return ApplicationFilterFacetPageResponse(
        surface="applications",
        axis=axis,
        items=items,
        selected_resolutions=_selected_facet_resolutions(
            filters,
            axis=axis,
            items=items,
            capabilities=capabilities,
        ),
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        capabilities=capabilities,
    )


@router.get(
    gateway_routes.APPLICATION_LABEL_FACETS_PATH,
    response_model=ApplicationLabelFacetPageResponse,
)
async def list_application_label_facets(
    request: Request,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    applications_environment: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_ENVIRONMENT_QUERY,
    ),
    applications_status: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_STATUS_QUERY,
    ),
    applications_pending_promotion: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_PENDING_PROMOTION_QUERY,
    ),
    applications_q: str | None = Query(
        default=None,
        alias=gateway_params.APPLICATIONS_SEARCH_QUERY,
    ),
    facet_q: str | None = Query(default=None, max_length=MAX_FILTER_SEARCH_LENGTH),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ApplicationLabelFacetPageResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        environments=applications_environment,
        statuses=applications_status,
        pending_promotion=applications_pending_promotion,
        labels=labels,
        query=applications_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = application_filter_fingerprint(filters)
    normalized_query = (facet_q or "").strip().casefold() or None
    if not authorized.application_ids:
        return _empty_label_response(authorized, fingerprint, filters)
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface="applications:label-facets",
        fingerprint=fingerprint,
        facet_query=normalized_query,
        position_keys=("key", "value"),
    )
    _reject_unpinned_cursor(position)
    result = await asyncio.to_thread(
        db.list_application_label_facets,
        workspace_id=authorized.workspace_id,
        allowed_cluster_ids=set(authorized.cluster_ids),
        allowed_application_ids=set(authorized.application_ids),
        filters=filters,
        facet_query=normalized_query,
        position=position,
        limit=limit,
    )
    _reject_unpinned_result_page(result, result.get("next_position"))
    capabilities = _capabilities(result)
    labels_capability = next(item for item in capabilities if item.axis == "labels")
    items = [] if labels_capability.availability == "unavailable" else _label_items(result)
    next_position = result.get("next_position") if items else None
    return ApplicationLabelFacetPageResponse(
        surface="applications",
        items=items,
        selected_resolutions=_selected_label_resolutions(filters),
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(items and result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        capabilities=capabilities,
    )


async def _authorized_scope(db: Any, current: Any) -> AuthorizedApplicationScope:
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
    return AuthorizedApplicationScope(
        workspace_id=workspace_id,
        user_id=user_id,
        roles=roles,
        cluster_ids=frozenset(cluster_ids),
        application_ids=frozenset(application_ids),
        authorization_revision=revision,
    )


def _require_requested_scope(
    authorized: AuthorizedApplicationScope,
    filters: ApplicationFilters,
) -> None:
    requested_clusters = set(filters.clusters) | {
        cluster_id for cluster_id, _namespace in filters.namespaces
    }
    if not requested_clusters.issubset(authorized.cluster_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)
    if not set(filters.applications).issubset(authorized.application_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


def _parse_filters(**kwargs: Any) -> ApplicationFilters:
    try:
        return parse_application_filters(**kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_REQUEST_DETAIL) from exc


def _cursor_codec(request: Request) -> FilterCursorCodec:
    configured = getattr(request.app.state, "application_filter_cursor_codec", None)
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
    authorized: AuthorizedApplicationScope,
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


def _next_cursor(
    codec: FilterCursorCodec,
    scope: CursorScope,
    position: object,
) -> str | None:
    if not isinstance(position, Mapping):
        return None
    try:
        return codec.encode(scope, position=position)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_REQUEST_DETAIL) from exc


def _reject_unpinned_cursor(position: Mapping[str, Any] | None) -> None:
    if position is not None:
        raise HTTPException(status_code=503, detail=PAGINATION_UNAVAILABLE_DETAIL)


def _reject_unpinned_result_page(
    result: Mapping[str, Any],
    next_position: object,
) -> None:
    if isinstance(next_position, Mapping) or bool(result.get("has_more")):
        raise HTTPException(status_code=503, detail=PAGINATION_UNAVAILABLE_DETAIL)


def _results_response(
    result: Mapping[str, Any],
    *,
    codec: FilterCursorCodec,
    scope: CursorScope,
    authorized: AuthorizedApplicationScope,
    fingerprint: str,
    filters: ApplicationFilters,
) -> ApplicationFilterResultsResponse:
    next_position = result.get("next_position")
    _reject_unpinned_result_page(result, next_position)
    return ApplicationFilterResultsResponse(
        items=[_safe_application_item(row) for row in list(result.get("items") or [])],
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        facets=_facet_items(result),
        capabilities=_capabilities(result),
        selected_labels=_selected_label_resolutions(filters),
    )


def _safe_application_item(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise HTTPException(status_code=500, detail="application filter projection is invalid")
    return {key: row.get(key) for key in ApplicationFilterItem.model_fields}


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
        filtered_count_completeness="partial" if filtered_available else "unavailable",
        unfiltered_count_completeness="partial" if unfiltered_available else "unavailable",
    )


def _snapshot(
    authorized: AuthorizedApplicationScope,
    fingerprint: str,
    result: Mapping[str, Any],
) -> FilterSnapshotMeta:
    reasons = {
        MUTABLE_PROJECTION_REASON,
        *(str(reason) for reason in (result.get("partial_reason_codes") or []) if reason),
    }
    return FilterSnapshotMeta(
        snapshot_revision=0,
        authorization_revision=authorized.authorization_revision,
        filter_fingerprint=fingerprint,
        observed_at=(str(result["observed_at"]) if result.get("observed_at") else None),
        stale=False,
        partial_reason_codes=sorted(reasons),
    )


def _capabilities(result: Mapping[str, Any]) -> list[ApplicationFilterCapability]:
    supplied = result.get("capabilities")
    if isinstance(supplied, list):
        return [ApplicationFilterCapability.model_validate(item) for item in supplied]
    availability = result.get("axis_availability")
    overrides = availability if isinstance(availability, Mapping) else {}
    defaults: dict[str, tuple[str, str | None, str]] = {
        "clusters": (
            "partial",
            "derived_deployment_binding_identity",
            "authorized_deployment_binding",
        ),
        "namespaces": (
            "partial",
            "derived_deployment_binding_identity",
            "authorized_deployment_binding",
        ),
        "applications": ("available", None, "authorized_application_record"),
        "environment": (
            "partial",
            "derived_deployment_binding_identity",
            "authorized_deployment_binding",
        ),
        "status": ("available", None, "application_lifecycle_status"),
        "pending_promotion": (
            "partial",
            "authorized_cluster_workflow_projection",
            "workflow_waiting_for_approval",
        ),
        "labels": (
            "unavailable",
            "application_label_projection_unavailable",
            "application_resource_snapshot",
        ),
    }
    result_items: list[ApplicationFilterCapability] = []
    for axis, (default_availability, default_reason, semantics) in defaults.items():
        override = overrides.get(axis)
        axis_availability = str(override) if isinstance(override, str) else default_availability
        reason = None if axis_availability == "available" else default_reason
        result_items.append(
            ApplicationFilterCapability(
                axis=axis,  # type: ignore[arg-type]
                availability=axis_availability,  # type: ignore[arg-type]
                reason_code=reason,
                source_semantics=semantics,
            )
        )
    return result_items


def _facet_items(
    result: Mapping[str, Any],
    *,
    requested_axis: ApplicationFacetAxis | None = None,
) -> list[dict[str, Any]]:
    source = result.get("items") if requested_axis is not None else result.get("facets")
    if not isinstance(source, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, Mapping):
            continue
        match_count = item.get("match_count")
        availability = str(item.get("availability") or "partial")
        normalized.append(
            {
                "axis": str(item.get("axis") or requested_axis or ""),
                "value": str(item.get("value") or ""),
                "label": str(item.get("label") or item.get("value") or ""),
                "match_count": (
                    int(match_count)
                    if isinstance(match_count, int) and not isinstance(match_count, bool)
                    else None
                ),
                "count_completeness": (
                    "partial" if isinstance(match_count, int) else "unavailable"
                ),
                "availability": availability,
            }
        )
    return normalized


def _label_items(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(result.get("items") or []):
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "")
        value = str(item.get("value") or "")
        match_count = item.get("match_count")
        normalized.append(
            {
                "key": key,
                "value": value,
                "selector": str(item.get("selector") or f"{key}={value}"),
                "match_count": (
                    int(match_count)
                    if isinstance(match_count, int) and not isinstance(match_count, bool)
                    else None
                ),
                "count_completeness": "partial",
            }
        )
    return normalized


def _selected_facet_resolutions(
    filters: ApplicationFilters,
    *,
    axis: ApplicationFacetAxis,
    items: list[dict[str, Any]],
    capabilities: list[ApplicationFilterCapability],
) -> list[dict[str, Any]]:
    capability = next(item for item in capabilities if item.axis == axis)
    by_value = {str(item["value"]): item for item in items}
    singular = {
        "clusters": "cluster",
        "namespaces": "namespace",
        "applications": "application",
    }.get(axis, axis)
    return [
        {
            "axis": singular,
            "value": value,
            "status": (
                "unavailable"
                if capability.availability == "unavailable" or value not in by_value
                else "resolved"
                if int(by_value[value].get("match_count") or 0) > 0
                else "zero"
            ),
            "display_label": by_value.get(value, {}).get("label"),
        }
        for value in selected_facet_values(filters, axis)
    ]


def _selected_label_resolutions(filters: ApplicationFilters) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "value": value,
            "selector": f"{key}={value}",
            "status": "unavailable",
        }
        for key, value in filters.labels
    ]


def _empty_results(
    authorized: AuthorizedApplicationScope,
    fingerprint: str,
    filters: ApplicationFilters,
) -> ApplicationFilterResultsResponse:
    return ApplicationFilterResultsResponse(
        items=[],
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        facets=[],
        capabilities=_empty_capabilities(),
        selected_labels=_selected_label_resolutions(filters),
    )


def _empty_facet_response(
    axis: ApplicationFacetAxis,
    authorized: AuthorizedApplicationScope,
    fingerprint: str,
    filters: ApplicationFilters,
) -> ApplicationFilterFacetPageResponse:
    return ApplicationFilterFacetPageResponse(
        surface="applications",
        axis=axis,
        items=[],
        selected_resolutions=[
            {
                "axis": {
                    "clusters": "cluster",
                    "namespaces": "namespace",
                    "applications": "application",
                }.get(axis, axis),
                "value": value,
                "status": "unavailable",
                "display_label": None,
            }
            for value in selected_facet_values(filters, axis)
        ],
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        capabilities=_empty_capabilities(),
    )


def _empty_label_response(
    authorized: AuthorizedApplicationScope,
    fingerprint: str,
    filters: ApplicationFilters,
) -> ApplicationLabelFacetPageResponse:
    return ApplicationLabelFacetPageResponse(
        surface="applications",
        items=[],
        selected_resolutions=_selected_label_resolutions(filters),
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        capabilities=_empty_capabilities(),
    )


def _exact_empty_counts() -> FilterResultCounts:
    return FilterResultCounts(
        filtered_count=0,
        unfiltered_count=0,
        filtered_count_completeness="exact",
        unfiltered_count_completeness="exact",
    )


def _empty_snapshot(
    authorized: AuthorizedApplicationScope,
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


def _empty_capabilities() -> list[ApplicationFilterCapability]:
    return [
        ApplicationFilterCapability(
            axis=axis,  # type: ignore[arg-type]
            availability="unavailable",
            reason_code="authorization_scope_empty",
            source_semantics=semantics,
        )
        for axis, semantics in (
            ("clusters", "authorized_deployment_binding"),
            ("namespaces", "authorized_deployment_binding"),
            ("applications", "authorized_application_record"),
            ("environment", "authorized_deployment_binding"),
            ("status", "application_lifecycle_status"),
            ("pending_promotion", "workflow_waiting_for_approval"),
            ("labels", "application_resource_snapshot"),
        )
    ]
