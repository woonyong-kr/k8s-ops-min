"""Tenant-safe Issues list and server-side facet endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

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
from domains.issue_filter.query import (
    IssueFacetAxis,
    IssueFilters,
    issue_filter_fingerprint,
    parse_issue_filters,
    selected_facet_values,
)
from packages.config.settings import env
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import params as gateway_params
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import (
    FilterResultCounts,
    FilterSnapshotMeta,
    IssueFilterCapability,
    IssueFilterFacetPageResponse,
    IssueFilterItem,
    IssueFilterResultsResponse,
    IssueLabelFacetPageResponse,
)
from packages.contracts.identity import Permission
from packages.runtime.dependencies import get_db

DEFAULT_PAGE_LIMIT = gateway_limits.FILTER_FACET_DEFAULT_LIMIT
MAX_PAGE_LIMIT = gateway_limits.FILTER_FACET_MAX_LIMIT
MAX_CURSOR_LENGTH = gateway_limits.FILTER_CURSOR_MAX_LENGTH
MAX_FILTER_SEARCH_LENGTH = gateway_limits.FILTER_SEARCH_MAX_LENGTH
FILTER_CURSOR_SIGNING_KEY_ENV = "FILTER_CURSOR_SIGNING_KEY"
INVALID_REQUEST_DETAIL = "issue filter request is invalid"
SCOPE_NOT_FOUND_DETAIL = "issue filter scope not found"
CURSOR_UNAVAILABLE_DETAIL = "issue filter cursor is unavailable"
MUTABLE_PROJECTION_REASON = "mutable_timeline_projection"

router = APIRouter()


@dataclass(frozen=True)
class AuthorizedIssueScope:
    workspace_id: str
    user_id: str
    roles: tuple[str, ...]
    cluster_ids: frozenset[str]
    application_ids: frozenset[str]
    authorization_revision: str


@router.get(
    gateway_routes.ISSUES_FILTER_RESULTS_PATH,
    response_model=IssueFilterResultsResponse,
)
async def list_filtered_issues(
    request: Request,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    issues_severity: str | None = Query(default=None, alias=gateway_params.ISSUES_SEVERITY_QUERY),
    issues_category: str | None = Query(default=None, alias=gateway_params.ISSUES_CATEGORY_QUERY),
    issues_status: str | None = Query(default=None, alias=gateway_params.ISSUES_STATUS_QUERY),
    issues_environment: str | None = Query(
        default=None,
        alias=gateway_params.ISSUES_ENVIRONMENT_QUERY,
    ),
    issues_q: str | None = Query(default=None, alias=gateway_params.ISSUES_SEARCH_QUERY),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> IssueFilterResultsResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        severities=issues_severity,
        categories=issues_category,
        statuses=issues_status,
        environments=issues_environment,
        labels=labels,
        query=issues_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = issue_filter_fingerprint(filters)
    if not authorized.cluster_ids:
        return _empty_results(authorized, fingerprint, filters)
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface="issues:list",
        fingerprint=fingerprint,
        facet_query=None,
        position_keys=("updated_at", "row_id"),
    )
    result = await asyncio.to_thread(
        db.list_filtered_issues,
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
    gateway_routes.ISSUES_FILTER_FACETS_PATH,
    response_model=IssueFilterFacetPageResponse,
)
async def list_issue_filter_facets(
    request: Request,
    axis: IssueFacetAxis,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    issues_severity: str | None = Query(default=None, alias=gateway_params.ISSUES_SEVERITY_QUERY),
    issues_category: str | None = Query(default=None, alias=gateway_params.ISSUES_CATEGORY_QUERY),
    issues_status: str | None = Query(default=None, alias=gateway_params.ISSUES_STATUS_QUERY),
    issues_environment: str | None = Query(
        default=None,
        alias=gateway_params.ISSUES_ENVIRONMENT_QUERY,
    ),
    issues_q: str | None = Query(default=None, alias=gateway_params.ISSUES_SEARCH_QUERY),
    facet_q: str | None = Query(default=None, max_length=MAX_FILTER_SEARCH_LENGTH),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> IssueFilterFacetPageResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        severities=issues_severity,
        categories=issues_category,
        statuses=issues_status,
        environments=issues_environment,
        labels=labels,
        query=issues_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = issue_filter_fingerprint(filters)
    normalized_query = (facet_q or "").strip().casefold() or None
    if not authorized.cluster_ids:
        return _empty_facet_response(axis, authorized, fingerprint)
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface=f"issues:facet:{axis}",
        fingerprint=fingerprint,
        facet_query=normalized_query,
        position_keys=("value",),
    )
    result = await asyncio.to_thread(
        db.list_issue_filter_facets,
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
    selected = result.get("selected_resolutions")
    if not isinstance(selected, list):
        selected = _selected_facet_resolutions(
            filters,
            axis=axis,
            items=items,
            capabilities=capabilities,
        )
    next_position = result.get("next_position")
    return IssueFilterFacetPageResponse(
        surface="issues",
        axis=axis,
        items=items,
        selected_resolutions=selected,
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        capabilities=capabilities,
    )


@router.get(
    gateway_routes.ISSUES_LABEL_FACETS_PATH,
    response_model=IssueLabelFacetPageResponse,
)
async def list_issue_label_facets(
    request: Request,
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    applications: str | None = Query(default=None),
    labels: str | None = Query(default=None),
    issues_severity: str | None = Query(default=None, alias=gateway_params.ISSUES_SEVERITY_QUERY),
    issues_category: str | None = Query(default=None, alias=gateway_params.ISSUES_CATEGORY_QUERY),
    issues_status: str | None = Query(default=None, alias=gateway_params.ISSUES_STATUS_QUERY),
    issues_environment: str | None = Query(
        default=None,
        alias=gateway_params.ISSUES_ENVIRONMENT_QUERY,
    ),
    issues_q: str | None = Query(default=None, alias=gateway_params.ISSUES_SEARCH_QUERY),
    facet_q: str | None = Query(default=None, max_length=MAX_FILTER_SEARCH_LENGTH),
    cursor: str | None = Query(default=None, min_length=1, max_length=MAX_CURSOR_LENGTH),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> IssueLabelFacetPageResponse:
    filters = _parse_filters(
        clusters=clusters,
        namespaces=namespaces,
        applications=applications,
        severities=issues_severity,
        categories=issues_category,
        statuses=issues_status,
        environments=issues_environment,
        labels=labels,
        query=issues_q,
    )
    authorized = await _authorized_scope(db, current)
    _require_requested_scope(authorized, filters)
    fingerprint = issue_filter_fingerprint(filters)
    normalized_query = (facet_q or "").strip().casefold() or None
    if not authorized.cluster_ids:
        return _empty_label_response(authorized, fingerprint, filters)
    codec = _cursor_codec(request)
    scope, position = _page_scope(
        codec,
        cursor=cursor,
        authorized=authorized,
        surface="issues:label-facets",
        fingerprint=fingerprint,
        facet_query=normalized_query,
        position_keys=("key", "value"),
    )
    result = await asyncio.to_thread(
        db.list_issue_label_facets,
        workspace_id=authorized.workspace_id,
        allowed_cluster_ids=set(authorized.cluster_ids),
        allowed_application_ids=set(authorized.application_ids),
        filters=filters,
        facet_query=normalized_query,
        position=position,
        limit=limit,
    )
    capabilities = _capabilities(result)
    labels_capability = next(item for item in capabilities if item.axis == "labels")
    items = [] if labels_capability.availability == "unavailable" else _label_items(result)
    selected = result.get("selected_resolutions")
    if not isinstance(selected, list):
        selected = _selected_label_resolutions(filters, result, labels_capability)
    next_position = result.get("next_position") if items else None
    return IssueLabelFacetPageResponse(
        surface="issues",
        items=items,
        selected_resolutions=selected,
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(items and result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        capabilities=capabilities,
    )


async def _authorized_scope(db: Any, current: Any) -> AuthorizedIssueScope:
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
                Permission.RCA_READ.value,
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
    return AuthorizedIssueScope(
        workspace_id=workspace_id,
        user_id=user_id,
        roles=roles,
        cluster_ids=frozenset(cluster_ids),
        application_ids=frozenset(application_ids),
        authorization_revision=revision,
    )


def _require_requested_scope(
    authorized: AuthorizedIssueScope,
    filters: IssueFilters,
) -> None:
    requested_clusters = set(filters.clusters) | {
        cluster_id for cluster_id, _namespace in filters.namespaces
    }
    if not requested_clusters.issubset(authorized.cluster_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)
    if not set(filters.applications).issubset(authorized.application_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


def _parse_filters(**kwargs: Any) -> IssueFilters:
    try:
        return parse_issue_filters(**kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_REQUEST_DETAIL) from exc


def _cursor_codec(request: Request) -> FilterCursorCodec:
    configured = getattr(request.app.state, "issue_filter_cursor_codec", None)
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
    authorized: AuthorizedIssueScope,
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


def _results_response(
    result: Mapping[str, Any],
    *,
    codec: FilterCursorCodec,
    scope: CursorScope,
    authorized: AuthorizedIssueScope,
    fingerprint: str,
    filters: IssueFilters,
) -> IssueFilterResultsResponse:
    next_position = result.get("next_position")
    capabilities = _capabilities(result)
    return IssueFilterResultsResponse(
        items=[_safe_issue_item(row) for row in list(result.get("items") or [])],
        next_cursor=_next_cursor(codec, scope, next_position),
        has_more=bool(result.get("has_more", isinstance(next_position, Mapping))),
        counts=_counts(result),
        snapshot=_snapshot(authorized, fingerprint, result),
        facets=_facet_items(result),
        capabilities=capabilities,
        selected_labels=_selected_label_resolutions(
            filters,
            result,
            next(item for item in capabilities if item.axis == "labels"),
            result_key="selected_labels",
        ),
    )


def _safe_issue_item(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise HTTPException(status_code=500, detail="issue filter projection is invalid")
    item = {key: row.get(key) for key in IssueFilterItem.model_fields}
    if item["category_completeness"] is None:
        item["category_completeness"] = "unavailable"
    return item


def _counts(result: Mapping[str, Any]) -> FilterResultCounts:
    source = result.get("counts")
    values = source if isinstance(source, Mapping) else result
    filtered = values.get("filtered_count")
    unfiltered = values.get("unfiltered_count")
    filtered_completeness = str(values.get("filtered_count_completeness") or "partial")
    unfiltered_completeness = str(values.get("unfiltered_count_completeness") or "partial")
    # rca_timeline is mutable and not revision-pinned. Instantaneous SQL counts are useful,
    # but must never be advertised as an immutable exact snapshot.
    if filtered_completeness == "exact":
        filtered_completeness = "partial"
    if unfiltered_completeness == "exact":
        unfiltered_completeness = "partial"
    return FilterResultCounts(
        filtered_count=int(filtered)
        if isinstance(filtered, int) and not isinstance(filtered, bool)
        else None,
        unfiltered_count=(
            int(unfiltered)
            if isinstance(unfiltered, int) and not isinstance(unfiltered, bool)
            else None
        ),
        filtered_count_completeness=filtered_completeness,
        unfiltered_count_completeness=unfiltered_completeness,
    )


def _snapshot(
    authorized: AuthorizedIssueScope,
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


def _capabilities(result: Mapping[str, Any]) -> list[IssueFilterCapability]:
    supplied = result.get("capabilities")
    if isinstance(supplied, list):
        return [IssueFilterCapability.model_validate(item) for item in supplied]
    availability = result.get("axis_availability")
    overrides = availability if isinstance(availability, Mapping) else {}
    items = list(result.get("items") or [])
    label_rows_present = any(
        isinstance(item, Mapping) and "key" in item and "value" in item for item in items
    )
    projections = {
        "category": _row_projection_availability(items, "category_completeness"),
        "environment": _row_projection_availability(items, "environment_completeness"),
        "applications": _row_projection_availability(
            items,
            "application_binding_completeness",
        ),
        "labels": (
            "partial"
            if label_rows_present
            else _row_projection_availability(items, "label_projection_completeness")
        ),
    }
    defaults: dict[str, tuple[str, str | None, str]] = {
        "clusters": ("available", None, "incident_event_cluster"),
        "namespaces": (
            "partial",
            "legacy_rows_may_omit_namespace",
            "incident_event_resource_identity",
        ),
        "applications": (
            projections["applications"],
            None
            if projections["applications"] == "available"
            else "event_time_application_binding_not_projected",
            "incident_event_time_snapshot",
        ),
        "severity": (
            "partial",
            "legacy_rows_may_omit_severity",
            "incident_event_severity",
        ),
        "category": (
            projections["category"],
            None
            if projections["category"] == "available"
            else "event_time_issue_category_not_projected",
            "incident_detector_category",
        ),
        "status": ("available", None, "timeline_issue_state_projection"),
        "environment": (
            projections["environment"],
            None
            if projections["environment"] == "available"
            else "event_time_environment_not_projected",
            "incident_event_time_snapshot",
        ),
        "labels": (
            projections["labels"],
            None
            if projections["labels"] == "available"
            else (
                "event_time_resource_labels_partially_projected"
                if projections["labels"] == "partial"
                else "event_time_resource_labels_not_projected"
            ),
            "incident_event_time_evidence_resource_snapshot",
        ),
    }
    capabilities: list[IssueFilterCapability] = []
    for axis, (default_availability, default_reason, semantics) in defaults.items():
        override = overrides.get(axis)
        if isinstance(override, Mapping):
            axis_availability = str(override.get("availability") or default_availability)
            reason = override.get("reason_code", default_reason)
            source_semantics = str(override.get("source_semantics") or semantics)
        elif isinstance(override, str):
            axis_availability = override
            reason = (
                None
                if override == "available"
                else default_reason or f"issue_{axis}_projection_{override}"
            )
            source_semantics = semantics
        else:
            axis_availability = default_availability
            reason = default_reason
            source_semantics = semantics
        capabilities.append(
            IssueFilterCapability(
                axis=axis,  # type: ignore[arg-type]
                availability=axis_availability,  # type: ignore[arg-type]
                reason_code=str(reason) if reason else None,
                source_semantics=source_semantics,
            )
        )
    return capabilities


def _row_projection_availability(items: list[Any], field: str) -> str:
    values = [str(item.get(field)) for item in items if isinstance(item, Mapping)]
    if values and all(value == "exact" for value in values):
        return "available"
    if any(value in {"exact", "partial"} for value in values):
        return "partial"
    return "unavailable"


def _facet_items(
    result: Mapping[str, Any],
    *,
    requested_axis: IssueFacetAxis | None = None,
) -> list[dict[str, Any]]:
    source = result.get("items") if requested_axis is not None else result.get("facets")
    rows: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(source, list):
        for item in source:
            if isinstance(item, Mapping):
                axis = str(item.get("axis") or requested_axis or "")
                rows.append((axis, item))
    elif isinstance(source, Mapping):
        aliases = {
            "clusters": "clusters",
            "namespaces": "namespaces",
            "applications": "applications",
            "severities": "severity",
            "severity": "severity",
            "categories": "category",
            "category": "category",
            "statuses": "status",
            "status": "status",
            "environments": "environment",
            "environment": "environment",
        }
        for key, values in source.items():
            axis = aliases.get(str(key))
            if axis is None or not isinstance(values, list):
                continue
            rows.extend((axis, item) for item in values if isinstance(item, Mapping))
    normalized: list[dict[str, Any]] = []
    for axis, item in rows:
        match_count = item.get("match_count")
        availability = str(
            item.get("availability")
            or ("available" if isinstance(match_count, int) else "unavailable")
        )
        completeness = str(
            item.get("count_completeness")
            or ("partial" if isinstance(match_count, int) else "unavailable")
        )
        normalized.append(
            {
                "axis": axis,
                "value": str(item.get("value") or ""),
                "label": str(item.get("label") or item.get("value") or ""),
                "match_count": (
                    int(match_count)
                    if isinstance(match_count, int) and not isinstance(match_count, bool)
                    else None
                ),
                "count_completeness": "partial" if completeness == "exact" else completeness,
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
    filters: IssueFilters,
    *,
    axis: IssueFacetAxis,
    items: list[dict[str, Any]],
    capabilities: list[IssueFilterCapability],
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
                if capability.availability == "unavailable"
                or (
                    capability.availability == "partial"
                    and int(by_value.get(value, {}).get("match_count") or 0) == 0
                )
                else "resolved"
                if value in by_value and int(by_value[value].get("match_count") or 0) > 0
                else "zero"
            ),
            "display_label": by_value.get(value, {}).get("label"),
        }
        for value in selected_facet_values(filters, axis)
    ]


def _selected_label_resolutions(
    filters: IssueFilters,
    result: Mapping[str, Any],
    capability: IssueFilterCapability,
    *,
    result_key: str = "selected_resolutions",
) -> list[dict[str, Any]]:
    supplied = result.get(result_key)
    if isinstance(supplied, list):
        return [dict(item) for item in supplied if isinstance(item, Mapping)]
    match_counts = {
        (str(item.get("key")), str(item.get("value"))): int(item.get("match_count") or 0)
        for item in list(result.get("selected_match_counts") or [])
        if isinstance(item, Mapping)
    }
    return [
        {
            "key": key,
            "value": value,
            "selector": f"{key}={value}",
            "status": (
                "unavailable"
                if capability.availability == "unavailable"
                or (capability.availability == "partial" and match_counts.get((key, value), 0) == 0)
                else "resolved"
                if match_counts.get((key, value), 0) > 0
                else "zero"
            ),
        }
        for key, value in filters.labels
    ]


def _empty_results(
    authorized: AuthorizedIssueScope,
    fingerprint: str,
    filters: IssueFilters,
) -> IssueFilterResultsResponse:
    result: dict[str, Any] = {"items": []}
    capabilities = _empty_capabilities()
    return IssueFilterResultsResponse(
        items=[],
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        facets=[],
        capabilities=capabilities,
        selected_labels=_selected_label_resolutions(
            filters,
            result,
            next(item for item in capabilities if item.axis == "labels"),
        ),
    )


def _empty_facet_response(
    axis: IssueFacetAxis,
    authorized: AuthorizedIssueScope,
    fingerprint: str,
) -> IssueFilterFacetPageResponse:
    return IssueFilterFacetPageResponse(
        surface="issues",
        axis=axis,
        items=[],
        selected_resolutions=[],
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        capabilities=_empty_capabilities(),
    )


def _empty_label_response(
    authorized: AuthorizedIssueScope,
    fingerprint: str,
    filters: IssueFilters,
) -> IssueLabelFacetPageResponse:
    capabilities = _empty_capabilities()
    return IssueLabelFacetPageResponse(
        surface="issues",
        items=[],
        selected_resolutions=_selected_label_resolutions(
            filters,
            {},
            next(item for item in capabilities if item.axis == "labels"),
        ),
        next_cursor=None,
        has_more=False,
        counts=_exact_empty_counts(),
        snapshot=_empty_snapshot(authorized, fingerprint),
        capabilities=capabilities,
    )


def _exact_empty_counts() -> FilterResultCounts:
    return FilterResultCounts(
        filtered_count=0,
        unfiltered_count=0,
        filtered_count_completeness="exact",
        unfiltered_count_completeness="exact",
    )


def _empty_snapshot(
    authorized: AuthorizedIssueScope,
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


def _empty_capabilities() -> list[IssueFilterCapability]:
    return [
        IssueFilterCapability(
            axis=axis,  # type: ignore[arg-type]
            availability="unavailable",
            reason_code="authorization_scope_empty",
            source_semantics=semantics,
        )
        for axis, semantics in (
            ("clusters", "incident_event_cluster"),
            ("namespaces", "incident_event_resource_identity"),
            ("applications", "incident_event_time_snapshot"),
            ("severity", "incident_event_severity"),
            ("category", "incident_detector_category"),
            ("status", "timeline_issue_state_projection"),
            ("environment", "incident_event_time_snapshot"),
            ("labels", "incident_event_time_evidence_resource_snapshot"),
        )
    ]
