"""Tenant-scoped server projection for the GitOps filter surface."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import Select, and_, false, func, literal, or_, select, tuple_

from domains.gitops.models import Approval, DeploymentBinding, GitRepository, WorkflowRun
from domains.gitops_filter.query import GitOpsFacetAxis, GitOpsFilters, without_facet_axis
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection, iso_or_none

MAX_PAGE_LIMIT = 200
MUTABLE_PROJECTION_REASON = "mutable_gitops_projection"
CHANGE_TYPE_UNAVAILABLE_REASON = "gitops_change_type_projection_unavailable"
LABEL_UNAVAILABLE_REASON = "gitops_label_projection_unavailable"


class GitOpsFilterRepository(DatabaseConnection):
    """Read-only GitOps queries with explicit tenant and resource grants."""

    def list_filtered_gitops_changes(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: GitOpsFilters,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not clusters or not applications:
            return _empty_result()
        effective_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        authorized = _authorized_changes(workspace_id, clusters, applications).cte(
            "authorized_gitops_changes"
        )
        filtered = _apply_gitops_filters(
            authorized,
            filters=filters,
            allowed_cluster_ids=clusters,
            allowed_application_ids=applications,
        ).cte("filtered_gitops_changes")
        projection_available = not filters.change_types and not filters.labels
        page: Select[Any] = (
            select(filtered) if projection_available else select(filtered).where(false())
        )
        if position:
            updated_at, change_id = _change_position(position)
            page = page.where(
                tuple_(filtered.c.updated_at, filtered.c.workflow_run_id)
                < tuple_(updated_at, change_id)
            )
        page = page.order_by(filtered.c.updated_at.desc(), filtered.c.workflow_run_id.desc()).limit(
            effective_limit + 1
        )
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(page).mappings().all()]
            has_more = len(rows) > effective_limit
            rows = rows[:effective_limit]
            counts, observed_at = _result_counts(
                conn,
                authorized,
                filtered,
                filtered_available=projection_available,
            )
        return {
            "items": [_serialize_change(row) for row in rows],
            "counts": counts,
            "facets": {},
            "capabilities": _capabilities(),
            "selected_labels": _selected_labels(filters),
            "has_more": has_more,
            "next_position": (
                {
                    "updated_at": iso_or_none(rows[-1].get("updated_at"))
                    or str(rows[-1]["updated_at"]),
                    "change_id": str(rows[-1]["workflow_run_id"]),
                }
                if has_more and rows
                else None
            ),
            "observed_at": observed_at,
            "partial_reason_codes": _partial_reasons(filters),
        }

    def list_gitops_filter_facets(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: GitOpsFilters,
        axis: GitOpsFacetAxis,
        facet_query: str | None,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not clusters or not applications:
            return _empty_facet_result()
        effective_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        authorized = _authorized_changes(workspace_id, clusters, applications).cte(
            "authorized_gitops_facets"
        )
        filtered = _apply_gitops_filters(
            authorized,
            filters=filters,
            allowed_cluster_ids=clusters,
            allowed_application_ids=applications,
        ).cte("filtered_gitops_count_scope")
        unavailable = bool(filters.labels or filters.change_types or axis == "change_type")
        if unavailable:
            with self.connection() as conn:
                counts, observed_at = _result_counts(
                    conn,
                    authorized,
                    filtered,
                    filtered_available=False,
                )
            return {
                **_empty_facet_result(),
                "counts": counts,
                "capabilities": _capabilities(),
                "selected_resolutions": _selected_facet_resolutions(filters, axis, []),
                "observed_at": observed_at,
                "partial_reason_codes": _partial_reasons(filters),
            }
        facet_filters = without_facet_axis(filters, axis)
        facet_base = _apply_gitops_filters(
            authorized,
            filters=facet_filters,
            allowed_cluster_ids=clusters,
            allowed_application_ids=applications,
        ).cte("gitops_facet_base")
        statement = _facet_statement(
            facet_base,
            axis=axis,
            facet_query=facet_query,
            position=position,
            limit=effective_limit,
        )
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
            has_more = len(rows) > effective_limit
            rows = rows[:effective_limit]
            counts, observed_at = _result_counts(
                conn,
                authorized,
                filtered,
                filtered_available=True,
            )
        items = [
            {
                "axis": axis,
                "value": str(row["value"]),
                "label": str(row["label"]),
                "match_count": int(row["match_count"]),
                "count_completeness": "partial",
                "availability": "partial",
            }
            for row in rows
        ]
        return {
            "items": items,
            "selected_resolutions": _selected_facet_resolutions(filters, axis, items),
            "counts": counts,
            "capabilities": _capabilities(),
            "has_more": has_more,
            "next_position": ({"value": str(rows[-1]["value"])} if has_more and rows else None),
            "observed_at": observed_at,
            "partial_reason_codes": [MUTABLE_PROJECTION_REASON],
        }


def _authorized_changes(
    workspace_id: str,
    allowed_cluster_ids: tuple[str, ...],
    allowed_application_ids: tuple[str, ...],
) -> Select[Any]:
    run = WorkflowRun.__table__
    binding = DeploymentBinding.__table__
    repository = GitRepository.__table__
    approval = Approval.__table__
    ranked_approvals = (
        select(
            approval.c.workflow_run_id,
            approval.c.workspace_id,
            approval.c.status,
            func.row_number()
            .over(
                partition_by=approval.c.workflow_run_id,
                order_by=(approval.c.updated_at.desc(), approval.c.approval_id.desc()),
            )
            .label("approval_rank"),
        )
        .where(approval.c.workspace_id == workspace_id)
        .cte("ranked_authorized_gitops_approvals")
    )
    latest_approval = (
        select(
            ranked_approvals.c.workflow_run_id,
            ranked_approvals.c.workspace_id,
            ranked_approvals.c.status,
        )
        .where(ranked_approvals.c.approval_rank == 1)
        .cte("latest_authorized_gitops_approval")
    )
    return (
        select(
            run.c.workflow_run_id,
            run.c.application_id,
            run.c.binding_id,
            binding.c.repository_id,
            run.c.cluster_id,
            binding.c.namespace,
            run.c.environment,
            run.c.commit_sha,
            run.c.status,
            run.c.current_step,
            run.c.summary,
            run.c.updated_at,
            repository.c.repo_ref,
            func.coalesce(latest_approval.c.status, literal("not_required")).label(
                "approval_status"
            ),
        )
        .select_from(
            run.join(
                binding,
                and_(
                    binding.c.workspace_id == run.c.workspace_id,
                    binding.c.binding_id == run.c.binding_id,
                ),
            )
            .join(
                repository,
                and_(
                    repository.c.workspace_id == run.c.workspace_id,
                    repository.c.repository_id == binding.c.repository_id,
                ),
            )
            .outerjoin(
                latest_approval,
                and_(
                    latest_approval.c.workspace_id == run.c.workspace_id,
                    latest_approval.c.workflow_run_id == run.c.workflow_run_id,
                ),
            )
        )
        .where(
            run.c.workspace_id == workspace_id,
            run.c.cluster_id.in_(allowed_cluster_ids) if allowed_cluster_ids else false(),
            run.c.application_id.in_(allowed_application_ids)
            if allowed_application_ids
            else false(),
        )
    )


def _apply_gitops_filters(
    source: Any,
    *,
    filters: GitOpsFilters,
    allowed_cluster_ids: tuple[str, ...],
    allowed_application_ids: tuple[str, ...],
) -> Select[Any]:
    statement: Select[Any] = select(source)
    if filters.clusters:
        if not set(filters.clusters).issubset(allowed_cluster_ids):
            statement = statement.where(false())
        else:
            statement = statement.where(source.c.cluster_id.in_(filters.clusters))
    if filters.namespaces:
        statement = statement.where(
            or_(
                *(
                    and_(source.c.cluster_id == cluster_id, source.c.namespace == namespace)
                    for cluster_id, namespace in filters.namespaces
                )
            )
        )
    if filters.applications:
        if not set(filters.applications).issubset(allowed_application_ids):
            statement = statement.where(false())
        else:
            statement = statement.where(source.c.application_id.in_(filters.applications))
    if filters.environments:
        statement = statement.where(func.lower(source.c.environment).in_(filters.environments))
    if filters.approvals:
        statement = statement.where(func.lower(source.c.approval_status).in_(filters.approvals))
    if filters.query:
        pattern = f"%{_escape_like(filters.query.casefold())}%"
        statement = statement.where(
            or_(
                func.lower(func.coalesce(source.c.summary, "")).like(pattern, escape="\\"),
                func.lower(source.c.repo_ref).like(pattern, escape="\\"),
                func.lower(source.c.commit_sha).like(pattern, escape="\\"),
                func.lower(source.c.current_step).like(pattern, escape="\\"),
            )
        )
    if filters.change_types or filters.labels:
        statement = statement.where(false())
    return statement


def _serialize_change(row: Mapping[str, Any]) -> JsonObject:
    return {
        "change_id": str(row["workflow_run_id"]),
        "application_id": str(row["application_id"]),
        "repository_id": str(row["repository_id"]),
        "binding_id": str(row["binding_id"]),
        "cluster_id": str(row["cluster_id"]),
        "namespace": str(row["namespace"]),
        "environment": str(row["environment"]).casefold(),
        "revision": str(row["commit_sha"]),
        "status": str(row["status"]).casefold(),
        "current_step": str(row["current_step"]),
        "approval_status": str(row.get("approval_status") or "not_required").casefold(),
        "change_type": None,
        "summary": str(row["summary"]) if row.get("summary") is not None else None,
        "updated_at": iso_or_none(row.get("updated_at")) or str(row.get("updated_at")),
        "change_type_completeness": "unavailable",
        "label_projection_completeness": "unavailable",
    }


def _facet_statement(
    source: Any,
    *,
    axis: GitOpsFacetAxis,
    facet_query: str | None,
    position: Mapping[str, Any] | None,
    limit: int,
) -> Select[Any]:
    if axis == "clusters":
        value = source.c.cluster_id
    elif axis == "namespaces":
        value = source.c.cluster_id + literal("/") + source.c.namespace
    elif axis == "applications":
        value = source.c.application_id
    elif axis == "environment":
        value = func.lower(source.c.environment)
    elif axis == "approval":
        value = func.lower(source.c.approval_status)
    else:
        return select(literal("").label("value"), literal("").label("label")).where(false())
    label = value
    statement = select(
        value.label("value"),
        label.label("label"),
        func.count(func.distinct(source.c.workflow_run_id)).label("match_count"),
    ).group_by(value, label)
    normalized_query = (facet_query or "").strip().casefold()
    if normalized_query:
        statement = statement.where(
            func.lower(label).like(f"%{_escape_like(normalized_query)}%", escape="\\")
        )
    if position:
        statement = statement.having(value > _facet_position(position))
    return statement.order_by(value).limit(limit + 1)


def _result_counts(
    conn: Any,
    authorized: Any,
    filtered: Any,
    *,
    filtered_available: bool,
) -> tuple[JsonObject, str | None]:
    row = (
        conn.execute(
            select(
                select(func.count()).select_from(authorized).scalar_subquery().label("unfiltered"),
                select(func.count()).select_from(filtered).scalar_subquery().label("filtered"),
                select(func.max(authorized.c.updated_at))
                .select_from(authorized)
                .scalar_subquery()
                .label("observed_at"),
            )
        )
        .mappings()
        .one()
    )
    return (
        {
            "filtered_count": int(row["filtered"]) if filtered_available else None,
            "unfiltered_count": int(row["unfiltered"]),
            "filtered_count_completeness": "partial" if filtered_available else "unavailable",
            "unfiltered_count_completeness": "partial",
        },
        iso_or_none(row.get("observed_at")),
    )


def _capabilities() -> list[JsonObject]:
    return [
        _capability("clusters", "partial", MUTABLE_PROJECTION_REASON, "authorized_workflow_run"),
        _capability("namespaces", "partial", MUTABLE_PROJECTION_REASON, "deployment_binding"),
        _capability("applications", "partial", MUTABLE_PROJECTION_REASON, "workflow_application"),
        _capability("environment", "partial", MUTABLE_PROJECTION_REASON, "workflow_environment"),
        _capability("approval", "partial", MUTABLE_PROJECTION_REASON, "latest_workflow_approval"),
        _capability(
            "change_type",
            "unavailable",
            CHANGE_TYPE_UNAVAILABLE_REASON,
            "workflow_diff_projection",
        ),
        _capability(
            "labels",
            "unavailable",
            LABEL_UNAVAILABLE_REASON,
            "desired_manifest_resource_snapshot",
        ),
    ]


def _capability(axis: str, availability: str, reason: str | None, source: str) -> JsonObject:
    return {
        "axis": axis,
        "availability": availability,
        "reason_code": reason,
        "source_semantics": source,
    }


def _selected_facet_resolutions(
    filters: GitOpsFilters,
    axis: GitOpsFacetAxis,
    items: list[JsonObject],
) -> list[JsonObject]:
    from domains.gitops_filter.query import selected_facet_values

    by_value = {str(item["value"]): item for item in items}
    response_axis = {
        "clusters": "cluster",
        "namespaces": "namespace",
        "applications": "application",
    }.get(axis, axis)
    unavailable = axis == "change_type"
    return [
        {
            "axis": response_axis,
            "value": value,
            "status": "unavailable"
            if unavailable
            else ("resolved" if value in by_value else "zero"),
            "display_label": str(by_value[value]["label"]) if value in by_value else value,
        }
        for value in selected_facet_values(filters, axis)
    ]


def _selected_labels(filters: GitOpsFilters) -> list[JsonObject]:
    return [
        {"key": key, "value": value, "selector": f"{key}={value}", "status": "unavailable"}
        for key, value in filters.labels
    ]


def _partial_reasons(filters: GitOpsFilters) -> list[str]:
    reasons = {MUTABLE_PROJECTION_REASON, CHANGE_TYPE_UNAVAILABLE_REASON, LABEL_UNAVAILABLE_REASON}
    if not filters.change_types:
        reasons.discard(CHANGE_TYPE_UNAVAILABLE_REASON)
    if not filters.labels:
        reasons.discard(LABEL_UNAVAILABLE_REASON)
    return sorted(reasons)


def _ids(values: Collection[str]) -> tuple[str, ...]:
    return tuple(
        sorted({value.strip() for value in values if isinstance(value, str) and value.strip()})
    )


def _change_position(position: Mapping[str, Any]) -> tuple[str, str]:
    updated_at = position.get("updated_at")
    change_id = position.get("change_id")
    if not isinstance(updated_at, str) or not updated_at:
        raise ValueError("GitOps cursor position is invalid")
    if not isinstance(change_id, str) or not change_id:
        raise ValueError("GitOps cursor position is invalid")
    return updated_at, change_id


def _facet_position(position: Mapping[str, Any]) -> str:
    value = position.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("GitOps facet cursor position is invalid")
    return value


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _empty_result() -> JsonObject:
    return {
        "items": [],
        "counts": _exact_empty_counts(),
        "facets": {},
        "capabilities": _capabilities(),
        "selected_labels": [],
        "has_more": False,
        "next_position": None,
        "observed_at": None,
        "partial_reason_codes": [],
    }


def _empty_facet_result() -> JsonObject:
    return {
        "items": [],
        "selected_resolutions": [],
        "counts": _exact_empty_counts(),
        "capabilities": _capabilities(),
        "has_more": False,
        "next_position": None,
        "observed_at": None,
        "partial_reason_codes": [],
    }


def _exact_empty_counts() -> JsonObject:
    return {
        "filtered_count": 0,
        "unfiltered_count": 0,
        "filtered_count_completeness": "exact",
        "unfiltered_count_completeness": "exact",
    }
