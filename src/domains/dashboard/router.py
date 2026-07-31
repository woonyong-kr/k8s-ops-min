"""dashboard HTTP API — 권한이 적용된 RCA timeline 조회."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from domains.command.debug_queries import queue_debug_query
from domains.command.router import debug_query_plan, publish_operation_event
from domains.identity.dependencies import (
    RESOURCE_ACCESS_DENIED_MESSAGE,
    require_cluster_access,
    require_session,
    resolve_allowed_cluster_ids,
)
from domains.inventory_filter.snapshot_scope import project_snapshot_scope
from domains.issue_filter.query import IssueFilters, parse_issue_filters
from domains.rca_changes.router import recent_change_item
from packages.config.constants import CommandStatus
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    AgentDebugQueryRequest,
    MetricQueryPresetUpsertRequest,
    MetricWidgetUpsertRequest,
    PrometheusQueryDefinition,
)
from packages.contracts.gateway.responses import (
    AgentDebugQueryResponse,
    MetricQueryPresetItem,
    MetricQueryPresetListResponse,
    MetricQueryPresetResponse,
    MetricWidgetItem,
    MetricWidgetListResponse,
    MetricWidgetResponse,
    RcaIncidentResponse,
    RcaIssueItem,
    RcaIssueLegacyListResponse,
    RcaIssueListResponse,
    RcaIssueQueueItem,
    RcaIssueQueueRecentChange,
    RcaTimelineItem,
    RcaTimelineResponse,
    ResourceIssueItem,
    ResourceIssueListResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, AccessResourceType, Permission
from packages.runtime.dependencies import get_db, get_operation_events

DEFAULT_TIMELINE_LIMIT = gateway_limits.DASHBOARD_RCA_DEFAULT_LIMIT
MAX_TIMELINE_LIMIT = gateway_limits.DASHBOARD_RCA_MAX_LIMIT
DEFAULT_RESOURCE_ISSUE_LIMIT = gateway_limits.RESOURCE_ISSUE_DEFAULT_LIMIT
NOT_FOUND_CODE = 404
TIMELINE_ITEM_FIELDS = set(RcaTimelineItem.model_fields)
METRIC_QUERY_FIELDS = set(MetricQueryPresetItem.model_fields)
METRIC_WIDGET_FIELDS = set(MetricWidgetItem.model_fields)
METRIC_PRESET_NOT_FOUND = "metric query preset not found"
METRIC_WIDGET_NOT_FOUND = "metric widget not found"

router = APIRouter()


@router.post(
    gateway_routes.METRICS_VALIDATE_PATH,
    response_model=AgentDebugQueryResponse,
    status_code=202,
)
async def queue_metrics_validation(
    payload: AgentDebugQueryRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    operation_events: Any = Depends(get_operation_events),
) -> AgentDebugQueryResponse:
    """Queue PromQL validation through the authenticated target agent only."""
    if "cluster_id" not in payload.model_fields_set or not payload.cluster_id.strip():
        raise HTTPException(status_code=422, detail="explicit cluster_id is required")
    workspace_id = _workspace_id(current)
    _require_evidence_access(db, current, workspace_id, payload.cluster_id)
    try:
        query = PrometheusQueryDefinition.model_validate(payload.query)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Prometheus agent query is invalid",
        ) from exc
    payload = payload.model_copy(update={"query": query.model_dump(exclude_none=True)})
    queued = queue_debug_query(
        db,
        payload,
        workspace_id=workspace_id,
        requested_by=current.user_id,
    )
    if not queued.inserted:
        raise HTTPException(status_code=409, detail="Prometheus validation is already queued")
    await publish_operation_event(
        operation_events,
        command_id=queued.command_id,
        workspace_id=workspace_id,
        status=CommandStatus.QUEUED,
        payload={
            "cluster_id": payload.cluster_id,
            "action": str(queued.plan["action"]),
            "correlation_id": queued.correlation_id,
        },
    )
    return AgentDebugQueryResponse(
        accepted=True,
        command_id=queued.command_id,
        correlation_id=queued.correlation_id,
    )


@router.get(
    gateway_routes.CLUSTER_METRIC_QUERY_PRESETS_PATH,
    response_model=MetricQueryPresetListResponse,
)
async def list_metric_query_presets(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> MetricQueryPresetListResponse:
    workspace_id = _workspace_id(current)
    _require_dashboard_access(db, current, workspace_id, cluster_id)
    rows = await asyncio.to_thread(db.list_metric_query_presets, workspace_id, cluster_id)
    return MetricQueryPresetListResponse(items=[metric_query_item(row) for row in rows])


@router.post(
    gateway_routes.CLUSTER_METRIC_QUERY_PRESETS_PATH,
    response_model=MetricQueryPresetResponse,
)
async def upsert_metric_query_preset(
    cluster_id: str,
    payload: MetricQueryPresetUpsertRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> MetricQueryPresetResponse:
    workspace_id = _workspace_id(current)
    _require_dashboard_manage_access(db, current, workspace_id, cluster_id)
    row = {
        "preset_id": payload.preset_id or f"metric-query-{uuid.uuid4()}",
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "name": payload.name,
        "description": payload.description,
        "source": payload.source,
        "query": payload.query,
        "range_seconds": payload.range_seconds,
        "step_seconds": payload.step_seconds,
        "unit": payload.unit,
        "created_by": current.user_id,
        "metadata": payload.metadata,
    }
    saved = await asyncio.to_thread(
        db.upsert_metric_query_preset,
        row,
        conflict_by_name=payload.preset_id is None,
    )
    return MetricQueryPresetResponse(item=metric_query_item(saved))


@router.delete(gateway_routes.CLUSTER_METRIC_QUERY_PRESET_PATH, status_code=204)
async def delete_metric_query_preset(
    cluster_id: str,
    preset_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = _workspace_id(current)
    _require_dashboard_manage_access(db, current, workspace_id, cluster_id)
    deleted = await asyncio.to_thread(
        db.delete_metric_query_preset,
        workspace_id,
        cluster_id,
        preset_id,
    )
    if not deleted:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=METRIC_PRESET_NOT_FOUND)
    return Response(status_code=204)


@router.post(
    gateway_routes.CLUSTER_METRIC_QUERY_PRESET_RUN_PATH,
    response_model=AgentDebugQueryResponse,
)
async def run_metric_query_preset(
    cluster_id: str,
    preset_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> AgentDebugQueryResponse:
    workspace_id = _workspace_id(current)
    _require_evidence_access(db, current, workspace_id, cluster_id)
    preset = await asyncio.to_thread(
        db.get_metric_query_preset,
        workspace_id,
        cluster_id,
        preset_id,
    )
    if preset is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=METRIC_PRESET_NOT_FOUND)
    correlation_id = f"corr-debug-{uuid.uuid4()}"
    query_request = AgentDebugQueryRequest(
        cluster_id=cluster_id,
        query=metric_query_payload(preset),
        reason=f"metric query preset: {preset['name']}",
    )
    plan = debug_query_plan(
        query_request,
        workspace_id=workspace_id,
        requested_by=current.user_id,
        correlation_id=correlation_id,
    )
    await asyncio.to_thread(db.queue_agent_command, correlation_id, plan, CommandStatus.QUEUED)
    return AgentDebugQueryResponse(
        accepted=True,
        command_id=str(plan["command_id"]),
        correlation_id=correlation_id,
    )


@router.get(
    gateway_routes.CLUSTER_METRIC_WIDGETS_PATH,
    response_model=MetricWidgetListResponse,
)
async def list_metric_widgets(
    cluster_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> MetricWidgetListResponse:
    workspace_id = _workspace_id(current)
    _require_dashboard_access(db, current, workspace_id, cluster_id)
    rows = await asyncio.to_thread(db.list_metric_widgets, workspace_id, cluster_id)
    return MetricWidgetListResponse(items=[metric_widget_item(row) for row in rows])


@router.post(
    gateway_routes.CLUSTER_METRIC_WIDGETS_PATH,
    response_model=MetricWidgetResponse,
)
async def upsert_metric_widget(
    cluster_id: str,
    payload: MetricWidgetUpsertRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> MetricWidgetResponse:
    workspace_id = _workspace_id(current)
    _require_dashboard_manage_access(db, current, workspace_id, cluster_id)
    preset = await asyncio.to_thread(
        db.get_metric_query_preset,
        workspace_id,
        cluster_id,
        payload.query_preset_id,
    )
    if preset is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=METRIC_PRESET_NOT_FOUND)
    row = {
        "widget_id": payload.widget_id or f"metric-widget-{uuid.uuid4()}",
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "query_preset_id": payload.query_preset_id,
        "title": payload.title,
        "kind": payload.kind,
        "position": payload.position,
        "settings": payload.settings,
        "created_by": current.user_id,
    }
    saved = await asyncio.to_thread(
        db.upsert_metric_widget,
        row,
        conflict_by_title=payload.widget_id is None,
    )
    return MetricWidgetResponse(item=metric_widget_item(saved))


@router.delete(gateway_routes.CLUSTER_METRIC_WIDGET_PATH, status_code=204)
async def delete_metric_widget(
    cluster_id: str,
    widget_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = _workspace_id(current)
    _require_dashboard_manage_access(db, current, workspace_id, cluster_id)
    deleted = await asyncio.to_thread(
        db.delete_metric_widget,
        workspace_id,
        cluster_id,
        widget_id,
    )
    if not deleted:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=METRIC_WIDGET_NOT_FOUND)
    return Response(status_code=204)


@router.get(
    gateway_routes.DASHBOARD_RCA_TIMELINE_PATH,
    response_model=RcaTimelineResponse,
)
async def rca_timeline(
    cluster_id: str | None = None,
    limit: int = Query(default=DEFAULT_TIMELINE_LIMIT, ge=1, le=MAX_TIMELINE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> RcaTimelineResponse:
    workspace_id = _workspace_id(current)
    allowed_cluster_ids = await _allowed_cluster_ids(db, current, workspace_id, cluster_id)
    rows = await asyncio.to_thread(
        db.list_rca_timeline,
        workspace_id,
        allowed_cluster_ids,
        limit,
    )
    return RcaTimelineResponse(items=[timeline_item(row) for row in rows])


@router.get(
    gateway_routes.DASHBOARD_RCA_ISSUES_PATH,
    response_model=RcaIssueLegacyListResponse | RcaIssueListResponse,
)
async def rca_issues(
    cluster_id: str | None = None,
    namespaces: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    category: str | None = Query(default=None),
    contract_version: str = Query(default="1", pattern="^[12]$"),
    limit: int = Query(default=DEFAULT_TIMELINE_LIMIT, ge=1, le=MAX_TIMELINE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> RcaIssueLegacyListResponse | RcaIssueListResponse:
    """Additive Issues queue contract; preserves the legacy timeline response shape."""
    workspace_id = _workspace_id(current)
    allowed_cluster_ids = await _allowed_cluster_ids(db, current, workspace_id, cluster_id)
    if contract_version == "1":
        rows = await asyncio.to_thread(
            db.list_rca_issues,
            workspace_id,
            allowed_cluster_ids,
            limit,
        )
        return RcaIssueLegacyListResponse(items=[issue_item(row) for row in rows])

    filters = _issue_queue_filters(
        cluster_id=cluster_id,
        namespaces=namespaces,
        severity=severity,
        category=category,
    )
    concrete_cluster_ids = await _concrete_issue_clusters(
        db,
        current,
        workspace_id,
        cluster_id=cluster_id,
        allowed_cluster_ids=allowed_cluster_ids,
    )
    requested_cluster_ids = {value for value, _namespace in filters.namespaces}
    if not requested_cluster_ids.issubset(concrete_cluster_ids):
        raise HTTPException(status_code=403, detail=RESOURCE_ACCESS_DENIED_MESSAGE)
    result = await asyncio.to_thread(
        db.list_rca_issue_queue,
        workspace_id,
        concrete_cluster_ids,
        namespaces=filters.namespaces,
        severities=filters.severities,
        categories=filters.categories,
        limit=limit,
        permission_scope_limited=cluster_id is None and allowed_cluster_ids is not None,
    )
    items = [queue_issue_item(row) for row in result.get("items") or []]
    incident_ids = tuple(sorted({item.incident_id for item in items if item.incident_id}))
    recent_rows = await asyncio.to_thread(
        db.list_recent_workload_changes_for_incidents,
        workspace_id,
        incident_ids,
        concrete_cluster_ids,
        limit=10,
    )
    recent_changes = [
        RcaIssueQueueRecentChange(
            incident_id=str(row["incident_id"]),
            **recent_change_item(row).model_dump(),
        )
        for row in recent_rows
    ]
    return RcaIssueListResponse(
        items=items,
        total=len(items),
        total_matched=int(result.get("total_matched") or 0),
        count_completeness="exact",
        recent_changes=recent_changes,
        visibility=result["visibility"],
        facets=result["facets"],
    )


@router.get(
    gateway_routes.RESOURCE_RCA_ISSUES_PATH,
    response_model=ResourceIssueListResponse,
)
async def resource_rca_issues(
    cluster_id: str = Query(min_length=1, max_length=253),
    kind: str = Query(min_length=1, max_length=253),
    name: str = Query(min_length=1, max_length=253),
    namespace: str | None = Query(default=None, max_length=253),
    limit: int = Query(default=DEFAULT_RESOURCE_ISSUE_LIMIT, ge=1, le=MAX_TIMELINE_LIMIT),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ResourceIssueListResponse:
    """Read issues for one exact resource without browser-side filtering or inference."""

    workspace_id = _workspace_id(current)
    for permission in (Permission.INVENTORY_READ.value, Permission.RCA_READ.value):
        require_cluster_access(
            db,
            current,
            workspace_id,
            cluster_id,
            permission,
            detail=RESOURCE_ACCESS_DENIED_MESSAGE,
        )
    contexts = await asyncio.to_thread(
        db.filter_snapshot_contexts,
        workspace_id,
        (cluster_id,),
    )
    projection = project_snapshot_scope(
        workspace_id=workspace_id,
        contexts=contexts,
        namespace_refs=((cluster_id, namespace),) if namespace else (),
        selected_cluster_ids=(cluster_id,),
    )
    rows = await asyncio.to_thread(
        db.list_resource_issues,
        workspace_id,
        cluster_id,
        namespace=namespace,
        resource_kind=kind,
        resource_name=name,
        limit=limit + 1,
    )
    items = [resource_issue_item(row) for row in rows[:limit]]
    return ResourceIssueListResponse(
        scope=projection.scopes[0],
        coverage_availability=projection.availability,
        observed_at=projection.observed_at,
        reason_codes=projection.reason_codes,
        items=items,
        limit=limit,
        has_more=len(rows) > limit,
    )


@router.get(
    gateway_routes.DASHBOARD_RCA_INCIDENT_PATH,
    response_model=RcaIncidentResponse,
)
async def rca_incident(
    incident_id: str,
    cluster_id: str | None = None,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> RcaIncidentResponse:
    workspace_id = _workspace_id(current)
    allowed_cluster_ids = await _allowed_cluster_ids(db, current, workspace_id, cluster_id)
    row = await asyncio.to_thread(
        db.get_rca_timeline_item,
        workspace_id,
        incident_id,
        allowed_cluster_ids,
    )
    if row is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="RCA incident not found")
    return RcaIncidentResponse(item=timeline_item(row))


async def _allowed_cluster_ids(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str | None,
) -> set[str] | None:
    if cluster_id is not None:
        require_cluster_access(
            db,
            current,
            workspace_id,
            cluster_id,
            Permission.RCA_READ.value,
            detail=RESOURCE_ACCESS_DENIED_MESSAGE,
        )
        return {cluster_id}
    return await asyncio.to_thread(
        db.accessible_resource_ids,
        current.user_id,
        workspace_id,
        AccessResourceType.CLUSTER.value,
        Permission.RCA_READ.value,
    )


def _issue_queue_filters(
    *,
    cluster_id: str | None,
    namespaces: str | None,
    severity: str | None,
    category: str | None,
) -> IssueFilters:
    try:
        filters = parse_issue_filters(
            clusters=cluster_id,
            namespaces=namespaces,
            applications=None,
            severities=severity,
            statuses=None,
            environments=None,
            labels=None,
            query=None,
            categories=category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="issue queue filters are invalid") from exc
    if not set(filters.severities).issubset({"critical", "warning"}):
        raise HTTPException(status_code=422, detail="issue queue filters are invalid")
    return filters


async def _concrete_issue_clusters(
    db: Any,
    current: Any,
    workspace_id: str,
    *,
    cluster_id: str | None,
    allowed_cluster_ids: set[str] | None,
) -> set[str]:
    if cluster_id is not None:
        return {cluster_id}
    if allowed_cluster_ids is not None:
        return set(allowed_cluster_ids)
    return await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.RCA_READ.value,
    )


def timeline_item(row: JsonObject) -> RcaTimelineItem:
    data = {key: row.get(key) for key in TIMELINE_ITEM_FIELDS}
    data["supporting_evidence"] = row.get("supporting_evidence") or []
    data["missing_evidence"] = row.get("missing_evidence") or []
    return RcaTimelineItem(**data)


def issue_item(row: JsonObject) -> RcaIssueItem:
    data = {key: row.get(key) for key in RcaIssueItem.model_fields}
    data["recovery_reason_code"] = _recovery_reason_code(row)
    _apply_rca_report_summary(data, row)
    data["supporting_evidence"] = data.get("supporting_evidence") or []
    data["missing_evidence"] = data.get("missing_evidence") or []
    return RcaIssueItem(**data)


def _apply_rca_report_summary(data: JsonObject, row: JsonObject) -> None:
    """Prefer a completed report and retain timeline payload prose as fallback."""
    report_summary = row.get("rca_issue_report_summary")
    report = report_summary if isinstance(report_summary, dict) else {}
    data["situation_summary"] = _nonempty_text(
        report.get("executive_summary"),
        data.get("situation_summary"),
    )
    data["recommended_action_summary"] = _nonempty_text(
        report.get("recommended_action"),
        data.get("recommended_action_summary"),
    )
    data["evidence_summary"] = _nonempty_text(
        report.get("evidence_summary"),
        data.get("evidence_summary"),
    )
    data["evidence_bundle_summary"] = _nonempty_text(
        report.get("evidence_bundle_summary"),
        data.get("evidence_bundle_summary"),
    )
    for field in ("supporting_evidence", "missing_evidence"):
        completed_report_evidence = _string_list_or_none(report.get(field))
        if completed_report_evidence is not None:
            data[field] = completed_report_evidence


def _nonempty_text(*values: object) -> str | None:
    return next(
        (
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        ),
        None,
    )


def _string_list_or_none(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str) and item]


def _recovery_reason_code(row: JsonObject) -> str | None:
    projected = row.get("recovery_reason_code")
    if isinstance(projected, str) and projected.strip():
        return projected.strip()
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("reason_code")
    return value.strip() if isinstance(value, str) and value.strip() else None


def queue_issue_item(row: JsonObject) -> RcaIssueQueueItem:
    data = {key: row.get(key) for key in RcaIssueQueueItem.model_fields}
    data["recovery_reason_code"] = _recovery_reason_code(row)
    _apply_rca_report_summary(data, row)
    data["supporting_evidence"] = data.get("supporting_evidence") or []
    data["missing_evidence"] = data.get("missing_evidence") or []
    return RcaIssueQueueItem(**data)


def resource_issue_item(row: JsonObject) -> ResourceIssueItem:
    data = {key: row.get(key) for key in ResourceIssueItem.model_fields}
    _apply_rca_report_summary(data, row)
    data["supporting_evidence"] = data.get("supporting_evidence") or []
    data["missing_evidence"] = data.get("missing_evidence") or []
    return ResourceIssueItem(**data)


def metric_query_item(row: JsonObject) -> MetricQueryPresetItem:
    data = {key: row.get(key) for key in METRIC_QUERY_FIELDS}
    data["metadata"] = row.get("metadata") or {}
    return MetricQueryPresetItem(**data)


def metric_widget_item(row: JsonObject) -> MetricWidgetItem:
    data = {key: row.get(key) for key in METRIC_WIDGET_FIELDS}
    data["position"] = row.get("position") or {}
    data["settings"] = row.get("settings") or {}
    return MetricWidgetItem(**data)


def metric_query_payload(row: JsonObject) -> JsonObject:
    payload: JsonObject = {
        "source": str(row["source"]),
        "name": str(row["name"]),
        "description": str(row.get("description") or ""),
        "query": str(row["query"]),
    }
    if row.get("range_seconds") is not None:
        payload["range_seconds"] = int(row["range_seconds"])
    if row.get("step_seconds") is not None:
        payload["step_seconds"] = int(row["step_seconds"])
    return payload


def _require_dashboard_access(db: Any, current: Any, workspace_id: str, cluster_id: str) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.DASHBOARD_READ.value,
        detail=RESOURCE_ACCESS_DENIED_MESSAGE,
    )


def _require_dashboard_manage_access(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.DASHBOARD_MANAGE.value,
        detail=RESOURCE_ACCESS_DENIED_MESSAGE,
    )


def _require_evidence_access(db: Any, current: Any, workspace_id: str, cluster_id: str) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.EVIDENCE_READ.value,
        detail=RESOURCE_ACCESS_DENIED_MESSAGE,
    )


def _workspace_id(current: Any) -> str:
    return getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
