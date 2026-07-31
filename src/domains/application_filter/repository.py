"""Tenant-scoped server-side projection for the Applications filter surface."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy import Select, and_, case, false, func, literal, or_, select, tuple_

from domains.application_filter.query import (
    ApplicationFacetAxis,
    ApplicationFilters,
    selected_facet_values,
    without_facet_axis,
)
from domains.gitops.models import Application, DeploymentBinding, GitRepository, WorkflowRun
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gitops import DeploymentBindingStatus, WorkflowRunStatus
from packages.storage.engine import DatabaseConnection, iso_or_none

MAX_PAGE_LIMIT = 200
MUTABLE_PROJECTION_REASON = "mutable_application_projection"
BINDING_PARTIAL_REASON = "derived_deployment_binding_identity"
LABEL_UNAVAILABLE_REASON = "application_label_projection_unavailable"


class ApplicationFilterRepository(DatabaseConnection):
    """Read-only application/filter queries with concrete tenant grants."""

    def list_filtered_applications(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: ApplicationFilters,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not applications:
            return _empty_result()
        effective_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        authorized = _authorized_applications(workspace_id, clusters, applications).cte(
            "authorized_filter_applications"
        )
        filtered = _apply_application_filters(
            authorized,
            filters=filters,
            allowed_cluster_ids=clusters,
            allowed_application_ids=applications,
        ).cte("filtered_applications")
        counts_available = not filters.labels
        if filters.labels:
            page: Select[Any] = select(filtered).where(false())
        else:
            page = select(filtered)
        if position:
            name, application_id = _application_position(position)
            page = page.where(
                tuple_(filtered.c.name, filtered.c.application_id) > tuple_(name, application_id)
            )
        page = page.order_by(filtered.c.name, filtered.c.application_id).limit(effective_limit + 1)
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(page).mappings().all()]
            has_more = len(rows) > effective_limit
            rows = rows[:effective_limit]
            counts, observed_at = _result_counts(
                conn,
                authorized,
                filtered,
                filtered_available=counts_available,
            )
            bindings = _bindings_for_applications(
                conn,
                workspace_id=workspace_id,
                application_ids={str(row["application_id"]) for row in rows},
                allowed_cluster_ids=clusters,
            )
        items = [_serialize_application(row, bindings=bindings) for row in rows]
        return {
            "items": items,
            "counts": counts,
            "facets": {},
            "capabilities": _capabilities(),
            "selected_labels": _selected_labels(filters),
            "has_more": has_more,
            "next_position": (
                {
                    "name": str(rows[-1]["name"]),
                    "application_id": str(rows[-1]["application_id"]),
                }
                if has_more and rows
                else None
            ),
            "observed_at": observed_at,
            "partial_reason_codes": _partial_reasons(filters),
        }

    def list_application_filter_facets(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: ApplicationFilters,
        axis: ApplicationFacetAxis,
        facet_query: str | None,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not applications:
            return _empty_facet_result()
        effective_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        authorized = _authorized_applications(workspace_id, clusters, applications).cte(
            "authorized_application_facets"
        )
        filtered = _apply_application_filters(
            authorized,
            filters=filters,
            allowed_cluster_ids=clusters,
            allowed_application_ids=applications,
        ).cte("filtered_application_count_scope")
        if filters.labels:
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
        facet_base = _apply_application_filters(
            authorized,
            filters=facet_filters,
            allowed_cluster_ids=clusters,
            allowed_application_ids=applications,
        ).cte("application_facet_base")
        statement = _facet_statement(
            facet_base,
            filters=facet_filters,
            axis=axis,
            allowed_cluster_ids=clusters,
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
        availability = _facet_availability(axis)
        items = [
            {
                "axis": axis,
                "value": str(row["value"]),
                "label": str(row["label"]),
                "match_count": int(row["match_count"]),
                "availability": availability,
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
            "partial_reason_codes": sorted({MUTABLE_PROJECTION_REASON, BINDING_PARTIAL_REASON}),
        }

    def list_application_label_facets(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: ApplicationFilters,
        facet_query: str | None,
        position: Mapping[str, Any] | None,
        limit: int,
    ) -> JsonObject:
        del facet_query, position, limit
        clusters = _ids(allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        if not workspace_id or not applications:
            return _empty_label_result()
        authorized = _authorized_applications(workspace_id, clusters, applications).cte(
            "authorized_application_labels"
        )
        without_labels = replace(filters, labels=())
        filtered = _apply_application_filters(
            authorized,
            filters=without_labels,
            allowed_cluster_ids=clusters,
            allowed_application_ids=applications,
        ).cte("application_label_count_scope")
        with self.connection() as conn:
            counts, observed_at = _result_counts(
                conn,
                authorized,
                filtered,
                filtered_available=not filters.labels,
            )
        return {
            "items": [],
            "selected_resolutions": _selected_labels(filters),
            "counts": counts,
            "capabilities": _capabilities(),
            "has_more": False,
            "next_position": None,
            "observed_at": observed_at,
            "partial_reason_codes": _partial_reasons(filters),
        }


def _authorized_applications(
    workspace_id: str,
    allowed_cluster_ids: tuple[str, ...],
    allowed_application_ids: tuple[str, ...],
) -> Select[Any]:
    application = Application.__table__
    binding = DeploymentBinding.__table__
    repository = GitRepository.__table__
    run = WorkflowRun.__table__
    run_predicates: list[Any] = [
        run.c.workspace_id == workspace_id,
        run.c.application_id.in_(allowed_application_ids),
    ]
    run_predicates.append(
        run.c.cluster_id.in_(allowed_cluster_ids) if allowed_cluster_ids else false()
    )
    ranked_runs = (
        select(
            run.c.application_id,
            run.c.status,
            run.c.updated_at,
            func.row_number()
            .over(
                partition_by=run.c.application_id,
                order_by=(run.c.updated_at.desc(), run.c.workflow_run_id.desc()),
            )
            .label("workflow_run_rank"),
        )
        .where(*run_predicates)
        .cte("ranked_authorized_application_runs")
    )
    latest_run = (
        select(
            ranked_runs.c.application_id,
            ranked_runs.c.status,
            ranked_runs.c.updated_at,
        )
        .where(ranked_runs.c.workflow_run_rank == 1)
        .cte("latest_authorized_application_runs")
    )
    binding_updates = (
        select(
            application.c.application_id,
            func.max(binding.c.updated_at).label("binding_updated_at"),
        )
        .select_from(
            application.join(
                binding,
                and_(
                    binding.c.workspace_id == application.c.workspace_id,
                    binding.c.repository_id == application.c.repository_id,
                    binding.c.app_name == application.c.name,
                ),
            )
        )
        .where(
            application.c.workspace_id == workspace_id,
            application.c.application_id.in_(allowed_application_ids),
            binding.c.status == DeploymentBindingStatus.ACTIVE.value,
            binding.c.cluster_id.in_(allowed_cluster_ids) if allowed_cluster_ids else false(),
        )
        .group_by(application.c.application_id)
        .cte("authorized_application_binding_updates")
    )
    observed_at = func.greatest(
        application.c.updated_at,
        func.coalesce(binding_updates.c.binding_updated_at, application.c.updated_at),
        func.coalesce(latest_run.c.updated_at, application.c.updated_at),
    ).label("updated_at")
    return (
        select(
            application.c.application_id,
            application.c.workspace_id,
            application.c.repository_id,
            application.c.name,
            application.c.manifest_path,
            application.c.status,
            observed_at,
            repository.c.repo_ref,
            (latest_run.c.status == WorkflowRunStatus.WAITING_FOR_APPROVAL.value).label(
                "pending_promotion"
            ),
        )
        .select_from(
            application.join(
                repository,
                and_(
                    repository.c.workspace_id == application.c.workspace_id,
                    repository.c.repository_id == application.c.repository_id,
                ),
            )
            .outerjoin(
                latest_run,
                latest_run.c.application_id == application.c.application_id,
            )
            .outerjoin(
                binding_updates,
                binding_updates.c.application_id == application.c.application_id,
            )
        )
        .where(
            application.c.workspace_id == workspace_id,
            application.c.application_id.in_(allowed_application_ids),
        )
    )


def _apply_application_filters(
    source: Any,
    *,
    filters: ApplicationFilters,
    allowed_cluster_ids: tuple[str, ...],
    allowed_application_ids: tuple[str, ...],
) -> Select[Any]:
    statement: Select[Any] = select(source)
    if filters.applications:
        requested = set(filters.applications)
        if not requested.issubset(allowed_application_ids):
            statement = statement.where(false())
        else:
            statement = statement.where(source.c.application_id.in_(filters.applications))
    if filters.statuses:
        statement = statement.where(func.lower(source.c.status).in_(filters.statuses))
    if filters.pending_promotion is not None:
        statement = statement.where(source.c.pending_promotion.is_(filters.pending_promotion))
    if filters.query:
        pattern = f"%{_escape_like(filters.query.casefold())}%"
        statement = statement.where(
            or_(
                func.lower(source.c.name).like(pattern, escape="\\"),
                func.lower(source.c.repo_ref).like(pattern, escape="\\"),
                func.lower(source.c.manifest_path).like(pattern, escape="\\"),
            )
        )
    if filters.clusters or filters.namespaces or filters.environments:
        statement = statement.where(
            _matching_binding_exists(
                source,
                filters=filters,
                allowed_cluster_ids=allowed_cluster_ids,
            )
        )
    if filters.labels:
        statement = statement.where(false())
    return statement


def _matching_binding_exists(
    source: Any,
    *,
    filters: ApplicationFilters,
    allowed_cluster_ids: tuple[str, ...],
) -> Any:
    binding = DeploymentBinding.__table__
    predicates = _binding_predicates(
        binding,
        source,
        filters=filters,
        allowed_cluster_ids=allowed_cluster_ids,
    )
    return select(literal(1)).select_from(binding).where(*predicates).exists()


def _binding_predicates(
    binding: Any,
    application: Any,
    *,
    filters: ApplicationFilters,
    allowed_cluster_ids: tuple[str, ...],
) -> list[Any]:
    predicates: list[Any] = [
        binding.c.workspace_id == application.c.workspace_id,
        binding.c.repository_id == application.c.repository_id,
        binding.c.app_name == application.c.name,
        binding.c.status == DeploymentBindingStatus.ACTIVE.value,
        binding.c.cluster_id.in_(allowed_cluster_ids) if allowed_cluster_ids else false(),
    ]
    if filters.clusters:
        predicates.append(binding.c.cluster_id.in_(filters.clusters))
    if filters.namespaces:
        predicates.append(
            or_(
                *(
                    and_(binding.c.cluster_id == cluster_id, binding.c.namespace == namespace)
                    for cluster_id, namespace in filters.namespaces
                )
            )
        )
    if filters.environments:
        predicates.append(func.lower(binding.c.environment).in_(filters.environments))
    return predicates


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


def _bindings_for_applications(
    conn: Any,
    *,
    workspace_id: str,
    application_ids: set[str],
    allowed_cluster_ids: tuple[str, ...],
) -> dict[str, list[JsonObject]]:
    if not application_ids or not allowed_cluster_ids:
        return {}
    application = Application.__table__
    binding = DeploymentBinding.__table__
    statement = (
        select(
            application.c.application_id,
            binding.c.binding_id,
            binding.c.cluster_id,
            binding.c.namespace,
            binding.c.environment,
        )
        .select_from(
            application.join(
                binding,
                and_(
                    binding.c.workspace_id == application.c.workspace_id,
                    binding.c.repository_id == application.c.repository_id,
                    binding.c.app_name == application.c.name,
                ),
            )
        )
        .where(
            application.c.workspace_id == workspace_id,
            application.c.application_id.in_(application_ids),
            binding.c.status == DeploymentBindingStatus.ACTIVE.value,
            binding.c.cluster_id.in_(allowed_cluster_ids),
        )
        .order_by(application.c.application_id, binding.c.binding_id)
    )
    by_application: dict[str, list[JsonObject]] = defaultdict(list)
    for row in conn.execute(statement).mappings():
        by_application[str(row["application_id"])].append(dict(row))
    return dict(by_application)


def _serialize_application(
    row: Mapping[str, Any],
    *,
    bindings: Mapping[str, list[JsonObject]],
) -> JsonObject:
    application_id = str(row["application_id"])
    visible_bindings = bindings.get(application_id, [])
    cluster_ids = sorted({str(binding["cluster_id"]) for binding in visible_bindings})
    namespace_refs = sorted(
        {
            f"{binding['cluster_id']}/{binding['namespace']}"
            for binding in visible_bindings
            if binding.get("namespace")
        }
    )
    environments = sorted(
        {
            str(binding["environment"]).casefold()
            for binding in visible_bindings
            if binding.get("environment")
        }
    )
    return {
        "application_id": application_id,
        "display_name": str(row["name"]),
        "repository_ids": [str(row["repository_id"])],
        "cluster_ids": cluster_ids,
        "namespace_refs": namespace_refs,
        "environments": environments,
        "lifecycle_status": str(row["status"]).casefold(),
        "pending_promotion": bool(row.get("pending_promotion")),
        "binding_count": len(visible_bindings),
        "updated_at": iso_or_none(row.get("updated_at")) or str(row.get("updated_at")),
        "binding_completeness": "partial",
        "label_projection_completeness": "unavailable",
    }


def _facet_statement(
    source: Any,
    *,
    filters: ApplicationFilters,
    axis: ApplicationFacetAxis,
    allowed_cluster_ids: tuple[str, ...],
    facet_query: str | None,
    position: Mapping[str, Any] | None,
    limit: int,
) -> Select[Any]:
    if axis in {"clusters", "namespaces", "environment"}:
        binding = DeploymentBinding.__table__
        predicates = _binding_predicates(
            binding,
            source,
            filters=filters,
            allowed_cluster_ids=allowed_cluster_ids,
        )
        if axis == "clusters":
            value = binding.c.cluster_id
        elif axis == "namespaces":
            value = binding.c.cluster_id + literal("/") + binding.c.namespace
            predicates.append(binding.c.namespace.is_not(None))
        else:
            value = func.lower(binding.c.environment)
            predicates.append(binding.c.environment.is_not(None))
        label = value
        statement = (
            select(
                value.label("value"),
                label.label("label"),
                func.count(func.distinct(source.c.application_id)).label("match_count"),
            )
            .select_from(source.join(binding, and_(*predicates)))
            .group_by(value, label)
        )
    else:
        if axis == "applications":
            value = source.c.application_id
            label = source.c.name
        elif axis == "status":
            value = func.lower(source.c.status)
            label = value
        else:
            value = case(
                (source.c.pending_promotion.is_(True), literal("true")),
                else_=literal("false"),
            )
            label = value
        statement = select(
            value.label("value"),
            label.label("label"),
            func.count(func.distinct(source.c.application_id)).label("match_count"),
        ).group_by(value, label)
    normalized_query = (facet_query or "").strip().casefold()
    if normalized_query:
        pattern = f"%{_escape_like(normalized_query)}%"
        statement = statement.where(func.lower(label).like(pattern, escape="\\"))
    if position:
        statement = statement.having(value > _facet_position(position))
    return statement.order_by(value).limit(limit + 1)


def _selected_facet_resolutions(
    filters: ApplicationFilters,
    axis: ApplicationFacetAxis,
    items: list[JsonObject],
) -> list[JsonObject]:
    by_value = {str(item["value"]): item for item in items}
    response_axis = {
        "clusters": "cluster",
        "namespaces": "namespace",
        "applications": "application",
    }.get(axis, axis)
    return [
        {
            "axis": response_axis,
            "value": value,
            "status": "resolved" if value in by_value else "zero",
            "display_label": (str(by_value[value]["label"]) if value in by_value else value),
        }
        for value in selected_facet_values(filters, axis)
    ]


def _selected_labels(filters: ApplicationFilters) -> list[JsonObject]:
    return [
        {
            "key": key,
            "value": value,
            "selector": f"{key}={value}",
            "status": "unavailable",
        }
        for key, value in filters.labels
    ]


def _capabilities() -> list[JsonObject]:
    return [
        _capability("clusters", "partial", BINDING_PARTIAL_REASON, "authorized_deployment_binding"),
        _capability(
            "namespaces", "partial", BINDING_PARTIAL_REASON, "authorized_deployment_binding"
        ),
        _capability("applications", "available", None, "authorized_application_record"),
        _capability(
            "environment", "partial", BINDING_PARTIAL_REASON, "authorized_deployment_binding"
        ),
        _capability("status", "available", None, "application_lifecycle_status"),
        _capability(
            "pending_promotion",
            "partial",
            "mutable_workflow_projection",
            "workflow_waiting_for_approval",
        ),
        _capability(
            "labels",
            "unavailable",
            LABEL_UNAVAILABLE_REASON,
            "application_resource_snapshot",
        ),
    ]


def _capability(
    axis: str,
    availability: str,
    reason_code: str | None,
    source_semantics: str,
) -> JsonObject:
    return {
        "axis": axis,
        "availability": availability,
        "reason_code": reason_code,
        "source_semantics": source_semantics,
    }


def _partial_reasons(filters: ApplicationFilters) -> list[str]:
    reasons = {MUTABLE_PROJECTION_REASON, BINDING_PARTIAL_REASON}
    if filters.labels:
        reasons.add(LABEL_UNAVAILABLE_REASON)
    return sorted(reasons)


def _facet_availability(axis: ApplicationFacetAxis) -> str:
    return "available" if axis in {"applications", "status"} else "partial"


def _ids(values: Collection[str]) -> tuple[str, ...]:
    return tuple(
        sorted({value.strip() for value in values if isinstance(value, str) and value.strip()})
    )


def _application_position(position: Mapping[str, Any]) -> tuple[str, str]:
    name = position.get("name")
    application_id = position.get("application_id")
    if not isinstance(name, str) or not name:
        raise ValueError("application cursor position is invalid")
    if not isinstance(application_id, str) or not application_id:
        raise ValueError("application cursor position is invalid")
    return name, application_id


def _facet_position(position: Mapping[str, Any]) -> str:
    value = position.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("application facet cursor position is invalid")
    return value


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _empty_result() -> JsonObject:
    return {
        "items": [],
        "counts": {
            "filtered_count": 0,
            "unfiltered_count": 0,
            "filtered_count_completeness": "exact",
            "unfiltered_count_completeness": "exact",
        },
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
        "counts": {
            "filtered_count": 0,
            "unfiltered_count": 0,
            "filtered_count_completeness": "exact",
            "unfiltered_count_completeness": "exact",
        },
        "capabilities": _capabilities(),
        "has_more": False,
        "next_position": None,
        "observed_at": None,
        "partial_reason_codes": [],
    }


def _empty_label_result() -> JsonObject:
    return {
        "items": [],
        "selected_resolutions": [],
        "counts": {
            "filtered_count": 0,
            "unfiltered_count": 0,
            "filtered_count_completeness": "exact",
            "unfiltered_count_completeness": "exact",
        },
        "capabilities": _capabilities(),
        "has_more": False,
        "next_position": None,
        "observed_at": None,
        "partial_reason_codes": [],
    }
