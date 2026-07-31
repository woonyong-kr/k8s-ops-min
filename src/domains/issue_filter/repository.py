"""Server-side Issues filters over the mutable RCA timeline projection."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, case, false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB

from domains.dashboard.models import RcaTimeline
from domains.dashboard.repository import (
    OPEN_INCIDENT_STATUSES,
    effective_confidence_column,
    effective_root_cause_column,
    fetch_latest_rca_issue_report_summaries,
    issue_detail_projection_columns,
    issue_severity_projection,
    serialize_timeline_row,
)
from domains.gitops.models import Application
from domains.identity.models import ClusterRegistration
from domains.issue_filter.query import IssueFacetAxis, IssueFilters, without_facet_axis
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection, iso_or_none

MAX_PAGE_LIMIT = 200
MAX_QUEUE_FACETS = 100


class IssueFilterRepository(DatabaseConnection):
    """Read-only, tenant-scoped projection queries used by the Issues surface."""

    def list_rca_issue_queue(
        self,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        *,
        namespaces: tuple[tuple[str, str], ...],
        severities: tuple[str, ...],
        categories: tuple[str, ...],
        limit: int,
        permission_scope_limited: bool = False,
    ) -> JsonObject:
        """Return one bounded queue page with exact statement-local matched counts."""
        clusters = _ids(allowed_cluster_ids)
        requested_namespaces = tuple(sorted(set(namespaces)))
        if not workspace_id or not clusters:
            return _empty_rca_issue_queue(requested_namespaces)
        filters = IssueFilters(
            clusters=(),
            namespaces=requested_namespaces,
            applications=(),
            severities=tuple(sorted(set(severities))),
            categories=tuple(sorted(set(categories))),
            statuses=(),
            environments=(),
            labels=(),
            query=None,
        )
        effective_limit = max(1, min(int(limit), 100))
        authorized = _authorized_issues(workspace_id, clusters).cte("authorized_issue_queue")
        filtered = _apply_issue_filters(
            authorized,
            filters=filters,
            allowed_application_ids=set(),
        ).cte("filtered_issue_queue")
        matched_count = select(func.count()).select_from(filtered).scalar_subquery()
        page = (
            select(filtered, matched_count.label("queue_total_matched"))
            .order_by(filtered.c.updated_at.desc(), filtered.c.id.desc())
            .limit(effective_limit)
        )
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(page).mappings().all()]
            total_matched = (
                int(rows[0]["queue_total_matched"])
                if rows
                else int(conn.execute(select(matched_count)).scalar_one())
            )
            visibility = _queue_visibility(
                conn,
                authorized,
                requested_namespaces=requested_namespaces,
                filters=filters,
                authorized_cluster_count=len(clusters),
                permission_scope_limited=permission_scope_limited,
            )
            facets, facets_truncated = _queue_facets(conn, filtered)
            report_summaries = fetch_latest_rca_issue_report_summaries(
                conn,
                workspace_id=workspace_id,
                correlation_ids=[
                    str(row["correlation_id"])
                    for row in rows
                    if row.get("correlation_id")
                ],
            )
        for row in rows:
            correlation_id = row.get("correlation_id")
            row["rca_issue_report_summary"] = (
                report_summaries.get(correlation_id)
                if isinstance(correlation_id, str)
                else None
            )
        if facets_truncated:
            visibility["state"] = "partial"
            visibility["completeness"] = "partial"
            visibility["reason_codes"] = sorted(
                {*visibility["reason_codes"], "issue_filter_facets_truncated"}
            )
        return {
            "items": [_serialize_queue_issue(row) for row in rows],
            "total_matched": total_matched,
            "visibility": visibility,
            "facets": facets,
        }

    def list_filtered_issues(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: IssueFilters,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not clusters:
            return _empty_result()
        effective_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        authorized = _authorized_issues(workspace_id, clusters).cte("authorized_issues")
        filtered = _apply_issue_filters(
            authorized,
            filters=filters,
            allowed_application_ids=applications,
        ).cte("filtered_issues")
        page: Select[Any] = select(filtered)
        if position:
            updated_at = _cursor_datetime(position.get("updated_at"))
            row_id = _cursor_int(position.get("row_id"))
            page = page.where(
                or_(
                    filtered.c.updated_at < updated_at,
                    and_(
                        filtered.c.updated_at == updated_at,
                        filtered.c.id < row_id,
                    ),
                )
            )
        page = page.order_by(filtered.c.updated_at.desc(), filtered.c.id.desc()).limit(
            effective_limit + 1
        )
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(page).mappings().all()]
            has_more = len(rows) > effective_limit
            rows = rows[:effective_limit]
            counts = _result_counts(conn, authorized, filtered)
            availability = _axis_availability(
                conn,
                authorized,
                allowed_application_ids=applications,
            )
            selected_match_counts = _selected_label_match_counts(
                conn,
                authorized,
                filters=filters,
                allowed_application_ids=applications,
            )
            observed_at = _max_updated_at(conn, authorized)
        filtered_available = _filters_available(filters, availability)
        items = [_serialize_issue(row, allowed_application_ids=applications) for row in rows]
        return {
            "items": items,
            "counts": _count_contract(
                counts,
                filtered_available=filtered_available,
            ),
            # Facet aggregation is intentionally isolated behind the two dedicated endpoints.
            # Recomputing all six axes here multiplied every list request into repeated scans.
            "facets": [],
            "selected_match_counts": selected_match_counts,
            "axis_availability": availability,
            "has_more": has_more,
            "next_position": (
                {
                    "updated_at": str(items[-1]["updated_at"]),
                    "row_id": str(rows[-1]["id"]),
                }
                if has_more and items
                else None
            ),
            "observed_at": observed_at,
            "partial_reason_codes": _partial_reasons(filters, availability),
        }

    def list_issue_filter_facets(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: IssueFilters,
        axis: IssueFacetAxis,
        facet_query: str | None,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not clusters:
            return _empty_result()
        effective_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        authorized = _authorized_issues(workspace_id, clusters).cte("authorized_issues")
        filtered = _apply_issue_filters(
            authorized,
            filters=filters,
            allowed_application_ids=applications,
        ).cte("filtered_issues")
        facet_base = _apply_issue_filters(
            authorized,
            filters=without_facet_axis(filters, axis),
            allowed_application_ids=applications,
        ).cte("issue_facet_base")
        with self.connection() as conn:
            availability = _axis_availability(
                conn,
                authorized,
                allowed_application_ids=applications,
            )
            counts = _result_counts(conn, authorized, filtered)
            observed_at = _max_updated_at(conn, authorized)
            if availability.get(axis) == "unavailable":
                rows: list[JsonObject] = []
                has_more = False
            else:
                statement = _facet_statement(
                    facet_base,
                    axis=axis,
                    allowed_application_ids=applications,
                    facet_query=facet_query,
                    position=position,
                    limit=effective_limit,
                )
                raw_rows = [dict(row) for row in conn.execute(statement).mappings().all()]
                has_more = len(raw_rows) > effective_limit
                raw_rows = raw_rows[:effective_limit]
                rows = _decorate_facets(
                    conn,
                    raw_rows,
                    axis=axis,
                    workspace_id=workspace_id,
                )
        return {
            "items": rows,
            "counts": _count_contract(
                counts,
                filtered_available=_filters_available(filters, availability),
            ),
            "axis_availability": availability,
            "has_more": has_more,
            "next_position": ({"value": str(rows[-1]["value"])} if has_more and rows else None),
            "observed_at": observed_at,
            "partial_reason_codes": _partial_reasons(filters, availability),
        }

    def list_issue_label_facets(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: IssueFilters,
        facet_query: str | None,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not clusters:
            return _empty_result()
        effective_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        authorized = _authorized_issues(workspace_id, clusters).cte("authorized_issues")
        filtered = _apply_issue_filters(
            authorized,
            filters=filters,
            allowed_application_ids=applications,
        ).cte("filtered_issues")
        with self.connection() as conn:
            availability = _axis_availability(
                conn,
                authorized,
                allowed_application_ids=applications,
            )
            counts = _result_counts(conn, authorized, filtered)
            observed_at = _max_updated_at(conn, authorized)
            if availability.get("labels") == "unavailable":
                rows: list[JsonObject] = []
                has_more = False
            else:
                statement = _label_facet_statement(
                    filtered,
                    facet_query=facet_query,
                    position=position,
                    limit=effective_limit,
                )
                raw_rows = [dict(row) for row in conn.execute(statement).mappings().all()]
                has_more = len(raw_rows) > effective_limit
                raw_rows = raw_rows[:effective_limit]
                rows = [
                    {
                        "key": str(row["key"]),
                        "value": str(row["value"]),
                        "match_count": int(row["match_count"]),
                    }
                    for row in raw_rows
                ]
            selected_match_counts = _selected_label_match_counts(
                conn,
                authorized,
                filters=filters,
                allowed_application_ids=applications,
            )
        return {
            "items": rows,
            "counts": _count_contract(
                counts,
                filtered_available=_filters_available(filters, availability),
            ),
            "selected_match_counts": selected_match_counts,
            "axis_availability": availability,
            "has_more": has_more,
            "next_position": (
                {"key": str(rows[-1]["key"]), "value": str(rows[-1]["value"])}
                if has_more and rows
                else None
            ),
            "observed_at": observed_at,
            "partial_reason_codes": _partial_reasons(filters, availability),
        }


def _authorized_issues(workspace_id: str, cluster_ids: set[str]) -> Select[Any]:
    table = RcaTimeline.__table__
    presentation_severity = case(
        (func.lower(table.c.severity).in_(("critical", "high")), literal("critical")),
        (func.lower(table.c.severity).in_(("warning", "medium")), literal("warning")),
        else_=None,
    ).label("severity")
    issue_id = case(
        (
            and_(table.c.cluster_id.is_not(None), table.c.incident_id.is_not(None)),
            literal("issue:") + table.c.cluster_id + literal(":") + table.c.incident_id,
        ),
        else_=literal("issue:") + table.c.correlation_id,
    ).label("issue_id")
    issue_state = case(
        (table.c.status == "incident_resolved", literal("resolved")),
        (table.c.status.in_(OPEN_INCIDENT_STATUSES), literal("open")),
        else_=literal("unknown"),
    ).label("issue_state")
    ranked = (
        select(
            table.c.id,
            table.c.workspace_id,
            issue_id,
            table.c.incident_id.label("detail_id"),
            table.c.correlation_id,
            table.c.cluster_id,
            table.c.incident_namespace.label("namespace"),
            table.c.incident_resource_kind.label("resource_kind"),
            table.c.incident_resource_name.label("resource_name"),
            table.c.incident_symptom.label("symptom"),
            presentation_severity,
            table.c.severity_complete,
            table.c.category,
            table.c.category_complete,
            issue_state,
            table.c.current_subject,
            table.c.status.label("pipeline_status"),
            table.c.environment,
            table.c.environment_complete,
            table.c.application_ids,
            table.c.application_ids_complete,
            table.c.labels,
            table.c.labels_complete,
            effective_root_cause_column(table),
            effective_confidence_column(table),
            *issue_detail_projection_columns(table),
            table.c.evidence_ref,
            table.c.supporting_evidence,
            table.c.missing_evidence,
            table.c.action_route,
            table.c.command_id,
            table.c.pr_url,
            table.c.error_reason,
            table.c.updated_at,
            func.row_number()
            .over(
                partition_by=(table.c.cluster_id, table.c.incident_id),
                order_by=(table.c.updated_at.desc(), table.c.id.desc()),
            )
            .label("issue_rank"),
        )
        .where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id.in_(cluster_ids),
            table.c.incident_id.is_not(None),
        )
        .cte("ranked_authorized_issues")
    )
    return select(*(column for column in ranked.c if column.name != "issue_rank")).where(
        ranked.c.issue_rank == 1
    )


def _apply_issue_filters(
    source: Any,
    *,
    filters: IssueFilters,
    allowed_application_ids: set[str],
) -> Select[Any]:
    statement: Select[Any] = select(source)
    if filters.clusters:
        statement = statement.where(source.c.cluster_id.in_(filters.clusters))
    if filters.namespaces:
        statement = statement.where(
            or_(
                *(
                    and_(
                        source.c.cluster_id == cluster_id,
                        source.c.namespace == namespace,
                    )
                    for cluster_id, namespace in filters.namespaces
                )
            )
        )
    if filters.applications:
        requested = set(filters.applications)
        visible = tuple(sorted(requested & allowed_application_ids))
        if requested - allowed_application_ids or not visible:
            statement = statement.where(false())
        else:
            statement = statement.where(
                source.c.application_ids_complete.is_(True),
                or_(*(source.c.application_ids.contains([value]) for value in visible)),
            )
    if filters.severities:
        statement = statement.where(
            source.c.severity_complete.is_(True),
            source.c.severity.in_(filters.severities),
        )
    if filters.categories:
        statement = statement.where(
            source.c.category_complete.is_(True),
            source.c.category.in_(filters.categories),
        )
    if filters.statuses:
        statement = statement.where(source.c.issue_state.in_(filters.statuses))
    if filters.environments:
        statement = statement.where(
            source.c.environment_complete.is_(True),
            source.c.environment.in_(filters.environments),
        )
    if filters.labels:
        statement = statement.where(
            source.c.labels_complete.is_(True),
            *(source.c.labels.contains({key: value}) for key, value in filters.labels),
        )
    if filters.query:
        pattern = f"%{_escape_like(filters.query.casefold())}%"
        statement = statement.where(
            or_(
                func.lower(func.coalesce(source.c.resource_name, "")).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(func.coalesce(source.c.symptom, "")).like(pattern, escape="\\"),
                func.lower(func.coalesce(source.c.root_cause, "")).like(
                    pattern,
                    escape="\\",
                ),
            )
        )
    return statement


def _result_counts(conn: Any, authorized: Any, filtered: Any) -> tuple[int, int]:
    row = (
        conn.execute(
            select(
                select(func.count())
                .select_from(filtered)
                .scalar_subquery()
                .label("filtered_count"),
                select(func.count())
                .select_from(authorized)
                .scalar_subquery()
                .label("unfiltered_count"),
            )
        )
        .mappings()
        .one()
    )
    return int(row["filtered_count"]), int(row["unfiltered_count"])


def _count_contract(
    counts: tuple[int, int],
    *,
    filtered_available: bool,
) -> JsonObject:
    filtered, unfiltered = counts
    return {
        "filtered_count": filtered if filtered_available else None,
        "unfiltered_count": unfiltered,
        "filtered_count_completeness": "partial" if filtered_available else "unavailable",
        "unfiltered_count_completeness": "partial",
    }


def _axis_availability(
    conn: Any,
    authorized: Any,
    *,
    allowed_application_ids: set[str],
) -> dict[str, str]:
    row = (
        conn.execute(
            select(
                func.count().label("total"),
                func.sum(
                    case(
                        (func.nullif(authorized.c.namespace, "").is_not(None), 1),
                        else_=0,
                    )
                ).label("namespaces"),
                func.sum(
                    case(
                        (
                            and_(
                                authorized.c.severity_complete.is_(True),
                                authorized.c.severity.is_not(None),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("severity"),
                func.sum(
                    case(
                        (
                            and_(
                                authorized.c.category_complete.is_(True),
                                authorized.c.category.is_not(None),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("category"),
                func.sum(
                    case(
                        (
                            and_(
                                authorized.c.environment_complete.is_(True),
                                authorized.c.environment.is_not(None),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("environment"),
                func.sum(
                    case(
                        (
                            and_(
                                authorized.c.application_ids_complete.is_(True),
                                authorized.c.application_ids.is_not(None),
                                func.jsonb_typeof(authorized.c.application_ids) == "array",
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("applications"),
                func.sum(
                    case(
                        (
                            and_(
                                authorized.c.labels_complete.is_(True),
                                authorized.c.labels.is_not(None),
                                func.jsonb_typeof(authorized.c.labels) == "object",
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("labels"),
            ).select_from(authorized)
        )
        .mappings()
        .one()
    )
    total = int(row["total"] or 0)
    application_availability = _availability(total, int(row["applications"] or 0))
    if application_availability != "unavailable" and _has_restricted_applications(
        conn,
        authorized,
        allowed_application_ids=allowed_application_ids,
    ):
        application_availability = "partial"
    return {
        "clusters": "available",
        "namespaces": _availability(total, int(row["namespaces"] or 0)),
        "applications": application_availability,
        "severity": _availability(total, int(row["severity"] or 0)),
        "category": _availability(total, int(row["category"] or 0)),
        "status": "available",
        "environment": _availability(total, int(row["environment"] or 0)),
        "labels": _availability(total, int(row["labels"] or 0)),
    }


def _has_restricted_applications(
    conn: Any,
    authorized: Any,
    *,
    allowed_application_ids: set[str],
) -> bool:
    expanded = func.jsonb_array_elements_text(
        _safe_json(authorized.c.application_ids, expected_type="array")
    ).table_valued("value")
    restricted = true()
    if allowed_application_ids:
        restricted = expanded.c.value.not_in(allowed_application_ids)
    statement = (
        select(literal(1))
        .select_from(authorized.join(expanded, true()))
        .where(
            authorized.c.application_ids_complete.is_(True),
            restricted,
        )
        .limit(1)
    )
    return conn.execute(statement).first() is not None


def _availability(total: int, complete: int) -> str:
    if total <= 0 or complete <= 0:
        return "unavailable"
    return "available" if complete >= total else "partial"


def _filters_available(filters: IssueFilters, availability: Mapping[str, str]) -> bool:
    required: list[str] = []
    if filters.namespaces:
        required.append("namespaces")
    if filters.applications:
        required.append("applications")
    if filters.severities:
        required.append("severity")
    if filters.categories:
        required.append("category")
    if filters.statuses:
        required.append("status")
    if filters.environments:
        required.append("environment")
    if filters.labels:
        required.append("labels")
    return all(availability.get(axis) != "unavailable" for axis in required)


def _partial_reasons(
    filters: IssueFilters,
    availability: Mapping[str, str],
) -> list[str]:
    reasons: set[str] = set()
    if filters.labels and availability.get("labels") == "unavailable":
        reasons.add("issue_label_projection_unavailable")
    for axis in (
        "namespaces",
        "severity",
        "category",
        "environment",
        "applications",
        "labels",
    ):
        state = availability.get(axis)
        if state and state != "available":
            reasons.add(f"issue_{axis}_projection_{state}")
    return sorted(reasons)


def _facet_statement(
    source: Any,
    *,
    axis: IssueFacetAxis,
    allowed_application_ids: set[str],
    facet_query: str | None,
    position: Mapping[str, Any] | None,
    limit: int,
) -> Select[Any]:
    if axis == "clusters":
        value = source.c.cluster_id
        base = select(value.label("value"), func.count().label("match_count")).where(
            value.is_not(None)
        )
    elif axis == "namespaces":
        value = source.c.cluster_id + literal("/") + source.c.namespace
        base = select(value.label("value"), func.count().label("match_count")).where(
            source.c.namespace.is_not(None)
        )
    elif axis == "applications":
        expanded = func.jsonb_array_elements_text(
            _safe_json(source.c.application_ids, expected_type="array")
        ).table_valued("value")
        value = expanded.c.value
        base = (
            select(value.label("value"), func.count().label("match_count"))
            .select_from(source.join(expanded, true()))
            .where(
                source.c.application_ids_complete.is_(True),
                value.in_(allowed_application_ids),
            )
        )
    elif axis == "severity":
        value = source.c.severity
        base = select(value.label("value"), func.count().label("match_count")).where(
            source.c.severity_complete.is_(True),
            source.c.severity.is_not(None),
        )
    elif axis == "category":
        value = source.c.category
        base = select(value.label("value"), func.count().label("match_count")).where(
            source.c.category_complete.is_(True),
            source.c.category.is_not(None),
        )
    elif axis == "status":
        value = source.c.issue_state
        base = select(value.label("value"), func.count().label("match_count"))
    else:
        value = source.c.environment
        base = select(value.label("value"), func.count().label("match_count")).where(
            source.c.environment_complete.is_(True),
            source.c.environment.is_not(None),
        )
    normalized_query = (facet_query or "").strip().casefold()
    if normalized_query:
        base = base.where(
            func.lower(value).like(
                f"%{_escape_like(normalized_query)}%",
                escape="\\",
            )
        )
    if position:
        base = base.where(value > _cursor_text(position.get("value")))
    return base.group_by(value).order_by(value).limit(limit + 1)


def _decorate_facets(
    conn: Any,
    rows: list[JsonObject],
    *,
    axis: IssueFacetAxis,
    workspace_id: str,
) -> list[JsonObject]:
    values = {str(row["value"]) for row in rows}
    labels: dict[str, str] = {}
    if axis == "clusters" and values:
        table = ClusterRegistration.__table__
        labels = {
            str(row["cluster_id"]): str(row["name"])
            for row in conn.execute(
                select(table.c.cluster_id, table.c.name).where(
                    table.c.workspace_id == workspace_id,
                    table.c.cluster_id.in_(values),
                )
            ).mappings()
        }
    elif axis == "applications" and values:
        table = Application.__table__
        labels = {
            str(row["application_id"]): str(row["name"])
            for row in conn.execute(
                select(table.c.application_id, table.c.name).where(
                    table.c.workspace_id == workspace_id,
                    table.c.application_id.in_(values),
                )
            ).mappings()
        }
    return [
        {
            "axis": axis,
            "value": str(row["value"]),
            "label": labels.get(str(row["value"]), _default_facet_label(axis, row["value"])),
            "match_count": int(row["match_count"]),
            "availability": "available",
        }
        for row in rows
    ]


def _default_facet_label(axis: IssueFacetAxis, value: Any) -> str:
    text = str(value)
    if axis == "namespaces":
        return text.rpartition("/")[2]
    return text


def _label_facet_statement(
    source: Any,
    *,
    facet_query: str | None,
    position: Mapping[str, Any] | None,
    limit: int,
) -> Select[Any]:
    expanded = func.jsonb_each_text(
        _safe_json(source.c.labels, expected_type="object")
    ).table_valued("key", "value")
    statement: Select[Any] = (
        select(
            expanded.c.key,
            expanded.c.value,
            func.count().label("match_count"),
        )
        .select_from(source.join(expanded, true()))
        .where(source.c.labels_complete.is_(True))
    )
    normalized_query = (facet_query or "").strip().casefold()
    if normalized_query:
        selector = func.lower(expanded.c.key + literal("=") + expanded.c.value)
        statement = statement.where(
            selector.like(f"%{_escape_like(normalized_query)}%", escape="\\")
        )
    if position:
        key = _cursor_text(position.get("key"))
        value = _cursor_text(position.get("value"))
        statement = statement.where(
            or_(expanded.c.key > key, and_(expanded.c.key == key, expanded.c.value > value))
        )
    return (
        statement.group_by(expanded.c.key, expanded.c.value)
        .order_by(
            expanded.c.key,
            expanded.c.value,
        )
        .limit(limit + 1)
    )


def _selected_label_match_counts(
    conn: Any,
    authorized: Any,
    *,
    filters: IssueFilters,
    allowed_application_ids: set[str],
) -> list[JsonObject]:
    if not filters.labels:
        return []
    without_labels = IssueFilters(
        clusters=filters.clusters,
        namespaces=filters.namespaces,
        applications=filters.applications,
        severities=filters.severities,
        categories=filters.categories,
        statuses=filters.statuses,
        environments=filters.environments,
        labels=(),
        query=filters.query,
    )
    base = _apply_issue_filters(
        authorized,
        filters=without_labels,
        allowed_application_ids=allowed_application_ids,
    ).cte("selected_issue_label_base")
    expanded = func.jsonb_each_text(_safe_json(base.c.labels, expected_type="object")).table_valued(
        "key", "value"
    )
    requested = tuple(filters.labels)
    statement = (
        select(
            expanded.c.key,
            expanded.c.value,
            func.count().label("match_count"),
        )
        .select_from(base.join(expanded, true()))
        .where(
            base.c.labels_complete.is_(True),
            or_(
                *(
                    and_(expanded.c.key == key, expanded.c.value == value)
                    for key, value in requested
                )
            ),
        )
        .group_by(expanded.c.key, expanded.c.value)
    )
    counts = {
        (str(row["key"]), str(row["value"])): int(row["match_count"])
        for row in conn.execute(statement).mappings()
    }
    return [
        {"key": key, "value": value, "match_count": counts.get((key, value), 0)}
        for key, value in requested
    ]


def _serialize_issue(
    row: Mapping[str, Any],
    *,
    allowed_application_ids: set[str],
) -> JsonObject:
    projected_applications = row.get("application_ids")
    projected_ids = (
        {str(value) for value in projected_applications if isinstance(value, str)}
        if isinstance(projected_applications, list)
        else set()
    )
    application_ids = sorted(projected_ids & allowed_application_ids)
    application_complete = bool(row.get("application_ids_complete"))
    application_completeness = (
        "unavailable"
        if not application_complete
        else "partial"
        if projected_ids - allowed_application_ids
        else "exact"
    )
    return {
        "issue_id": str(row["issue_id"]),
        "detail_id": str(row["detail_id"]) if row.get("detail_id") else None,
        "correlation_id": str(row["correlation_id"]),
        "cluster_id": str(row["cluster_id"]),
        "namespace": str(row["namespace"]) if row.get("namespace") else None,
        "resource_kind": str(row["resource_kind"]) if row.get("resource_kind") else None,
        "resource_name": str(row["resource_name"]) if row.get("resource_name") else None,
        "symptom": str(row["symptom"]) if row.get("symptom") else None,
        "severity": str(row["severity"]) if row.get("severity") else None,
        "category": str(row["category"]) if row.get("category") else None,
        "category_completeness": ("exact" if bool(row.get("category_complete")) else "unavailable"),
        "issue_state": str(row["issue_state"]),
        "current_subject": str(row["current_subject"]),
        "pipeline_status": str(row["pipeline_status"]),
        "environment": str(row["environment"]) if row.get("environment") else None,
        "environment_completeness": (
            "exact" if bool(row.get("environment_complete")) else "unavailable"
        ),
        "application_ids": application_ids,
        "application_binding_completeness": application_completeness,
        "label_projection_completeness": (
            "exact" if bool(row.get("labels_complete")) else "unavailable"
        ),
        "root_cause": str(row["root_cause"]) if row.get("root_cause") else None,
        "confidence": float(row["confidence"]) if row.get("confidence") is not None else None,
        "updated_at": iso_or_none(row.get("updated_at")) or str(row.get("updated_at")),
    }


def _serialize_queue_issue(row: Mapping[str, Any]) -> JsonObject:
    item = serialize_timeline_row(row)
    item.update(
        incident_id=row.get("detail_id"),
        incident_namespace=row.get("namespace"),
        incident_resource_kind=row.get("resource_kind"),
        incident_resource_name=row.get("resource_name"),
        incident_symptom=row.get("symptom"),
        status=row.get("pipeline_status"),
    )
    item.update(issue_severity_projection(row))
    category = str(row.get("category") or "").strip()
    category_complete = row.get("category_complete") is True
    item.update(
        category=category if category_complete and category else None,
        category_availability=("available" if category_complete and category else "unavailable"),
        category_reason_code=(None if category_complete and category else "source_incomplete"),
    )
    item.pop("queue_total_matched", None)
    return item


def _queue_visibility(
    conn: Any,
    authorized: Any,
    *,
    requested_namespaces: tuple[tuple[str, str], ...],
    filters: IssueFilters,
    authorized_cluster_count: int,
    permission_scope_limited: bool,
) -> JsonObject:
    row = (
        conn.execute(
            select(
                func.count().label("total"),
                func.sum(case((authorized.c.namespace.is_not(None), 1), else_=0)).label(
                    "namespaces_complete"
                ),
                func.sum(case((authorized.c.severity_complete.is_(True), 1), else_=0)).label(
                    "severity_complete"
                ),
                func.sum(case((authorized.c.category_complete.is_(True), 1), else_=0)).label(
                    "category_complete"
                ),
            ).select_from(authorized)
        )
        .mappings()
        .one()
    )
    total = int(row["total"] or 0)
    availability = {
        "namespaces": _availability(total, int(row["namespaces_complete"] or 0)),
        "severity": _availability(total, int(row["severity_complete"] or 0)),
        "category": _availability(total, int(row["category_complete"] or 0)),
    }
    reasons = {
        f"legacy_{axis}_projection_incomplete"
        for axis, value in availability.items()
        if total > 0 and value != "available"
    }
    if permission_scope_limited:
        reasons.add("cluster_scope_permission_limited")
    required_unavailable = (
        (bool(filters.namespaces) and availability["namespaces"] == "unavailable")
        or (bool(filters.severities) and availability["severity"] == "unavailable")
        or (bool(filters.categories) and availability["category"] == "unavailable")
    )
    if required_unavailable:
        completeness = "unavailable"
    elif reasons:
        completeness = "partial"
    else:
        completeness = "exact"
    return {
        "state": "complete" if not reasons else "partial",
        "completeness": completeness,
        "authorized_cluster_count": authorized_cluster_count,
        "requested_namespaces": [
            f"{cluster_id}/{namespace}" for cluster_id, namespace in requested_namespaces
        ],
        "reason_codes": sorted(reasons),
    }


def _queue_facets(conn: Any, filtered: Any) -> tuple[JsonObject, bool]:
    axes = {
        "namespaces": (
            filtered.c.cluster_id + literal("/") + filtered.c.namespace,
            filtered.c.namespace.is_not(None),
        ),
        "severities": (
            filtered.c.severity,
            and_(filtered.c.severity_complete.is_(True), filtered.c.severity.is_not(None)),
        ),
        "categories": (
            filtered.c.category,
            and_(filtered.c.category_complete.is_(True), filtered.c.category.is_not(None)),
        ),
    }
    facets: JsonObject = {}
    truncated = False
    for axis, (value, predicate) in axes.items():
        rows = (
            conn.execute(
                select(value.label("value"), func.count().label("count"))
                .select_from(filtered)
                .where(predicate)
                .group_by(value)
                .order_by(value)
                .limit(MAX_QUEUE_FACETS + 1)
            )
            .mappings()
            .all()
        )
        if len(rows) > MAX_QUEUE_FACETS:
            truncated = True
            rows = rows[:MAX_QUEUE_FACETS]
        facets[axis] = [{"value": str(item["value"]), "count": int(item["count"])} for item in rows]
    return facets, truncated


def _empty_rca_issue_queue(
    requested_namespaces: tuple[tuple[str, str], ...],
) -> JsonObject:
    return {
        "items": [],
        "total_matched": 0,
        "visibility": {
            "state": "restricted",
            "completeness": "unavailable",
            "authorized_cluster_count": 0,
            "requested_namespaces": [
                f"{cluster_id}/{namespace}" for cluster_id, namespace in requested_namespaces
            ],
            "reason_codes": ["no_authorized_clusters"],
        },
        "facets": {"namespaces": [], "severities": [], "categories": []},
    }


def _max_updated_at(conn: Any, source: Any) -> str | None:
    value = conn.execute(select(func.max(source.c.updated_at))).scalar_one_or_none()
    return iso_or_none(value)


def _ids(values: Collection[str]) -> set[str]:
    if not values:
        return set()
    return {value.strip() for value in values if isinstance(value, str) and value.strip()}


def _cursor_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("issue cursor position is invalid")
    return value


def _cursor_datetime(value: Any) -> datetime:
    text = _cursor_text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("issue cursor position is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("issue cursor position is invalid")
    return parsed


def _cursor_int(value: Any) -> int:
    text = _cursor_text(value)
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError("issue cursor position is invalid") from exc
    if parsed < 1 or str(parsed) != text:
        raise ValueError("issue cursor position is invalid")
    return parsed


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _safe_json(column: Any, *, expected_type: str) -> Any:
    fallback: list[Any] | dict[str, Any] = [] if expected_type == "array" else {}
    return case(
        (func.jsonb_typeof(column) == expected_type, column),
        else_=literal(fallback, type_=JSONB),
    )


def _empty_result() -> JsonObject:
    return {
        "items": [],
        "counts": {
            "filtered_count": 0,
            "unfiltered_count": 0,
            "filtered_count_completeness": "exact",
            "unfiltered_count_completeness": "exact",
        },
        "facets": [],
        "selected_match_counts": [],
        "axis_availability": {},
        "has_more": False,
        "next_position": None,
        "observed_at": None,
        "partial_reason_codes": [],
    }
