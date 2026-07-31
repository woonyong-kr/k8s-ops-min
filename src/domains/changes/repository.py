"""Tenant-safe evidence queries for the Resources change timeline."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Select,
    and_,
    case,
    cast,
    false,
    func,
    literal,
    or_,
    select,
    union_all,
)

from domains.dashboard.models import RcaTimeline
from domains.gitops.models import DeploymentBinding, WorkflowRun
from domains.inventory.change_correlation import (
    INVENTORY_CHANGE_LEDGER_EPOCH,
    REVISION_ID_FIELD,
    SOURCE_SNAPSHOT_ID_FIELD,
    VERSION_ID_FIELD,
)
from domains.inventory_filter.models import (
    InventoryFilterRevision,
    InventoryResourceApplicationVersion,
    InventoryResourceLabelVersion,
    InventoryResourceVersion,
)
from domains.inventory_filter.query import ResourceFilters
from domains.timeline.models import TimelineLedgerEvent
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection

MAX_CHANGE_EVENTS = 1_000
MAX_CHANGE_OBSERVATIONS = 200_000

CRITICAL_VALUES = frozenset(
    {
        "critical",
        "error",
        "failed",
        "failure",
        "fatal",
        "high",
        "unhealthy",
    }
)
WARNING_VALUES = frozenset(
    {
        "degraded",
        "medium",
        "pending",
        "running",
        "warning",
        "waiting",
        "waiting_for_approval",
    }
)
INFO_VALUES = frozenset(
    {
        "active",
        "completed",
        "healthy",
        "info",
        "low",
        "normal",
        "ready",
        "resolved",
        "succeeded",
        "success",
    }
)
WORKLOAD_KINDS = frozenset(
    {"cronjob", "daemonset", "deployment", "job", "replicaset", "statefulset"}
)


class ChangeTimelineRepository(DatabaseConnection):
    """Read immutable observations and allowlisted mutable projections only."""

    def list_change_timeline_evidence(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        allowed_incident_cluster_ids: Collection[str],
        allowed_deployment_application_ids: Collection[str],
        filters: ResourceFilters,
        from_ms: int,
        to_ms: int,
        limit: int = MAX_CHANGE_EVENTS,
    ) -> JsonObject:
        clusters = _selected_clusters(filters, allowed_cluster_ids)
        applications = _ids(allowed_application_ids)
        incident_clusters = clusters & _ids(allowed_incident_cluster_ids)
        deployment_applications = applications & _ids(allowed_deployment_application_ids)
        effective_limit = max(1, min(int(limit), MAX_CHANGE_EVENTS))
        if not workspace_id or not clusters:
            return {
                "events": [],
                "observations": [],
                "event_overflow": False,
                "observation_overflow": False,
            }

        start = _datetime_from_ms(from_ms)
        end = _datetime_from_ms(to_ms)
        with self.connection() as conn:
            inventory_rows = _execute_rows(
                conn,
                _inventory_events_statement(
                    workspace_id=workspace_id,
                    cluster_ids=clusters,
                    allowed_application_ids=applications,
                    filters=filters,
                    start=start,
                    end=end,
                    limit=effective_limit + 1,
                ),
            )
            incident_rows = (
                _execute_rows(
                    conn,
                    _incident_events_statement(
                        workspace_id=workspace_id,
                        cluster_ids=incident_clusters,
                        allowed_application_ids=applications,
                        filters=filters,
                        start=start,
                        end=end,
                        limit=effective_limit + 1,
                    ),
                )
                if incident_clusters
                else []
            )
            deployment_rows = (
                _execute_rows(
                    conn,
                    _deployment_events_statement(
                        workspace_id=workspace_id,
                        cluster_ids=clusters,
                        application_ids=deployment_applications,
                        filters=filters,
                        start=start,
                        end=end,
                        limit=effective_limit + 1,
                    ),
                )
                if deployment_applications
                else []
            )
            observation_rows = _execute_rows(
                conn,
                _observations_statement(
                    workspace_id=workspace_id,
                    cluster_ids=clusters,
                    filters=filters,
                    start=start,
                    end=end,
                    limit=MAX_CHANGE_OBSERVATIONS + 1,
                ),
            )

        events = [
            *(_inventory_event(row) for row in inventory_rows[: effective_limit + 1]),
            *(_incident_event(row) for row in incident_rows[: effective_limit + 1]),
            *(_deployment_event(row) for row in deployment_rows[: effective_limit + 1]),
        ]
        events.sort(key=_event_sort_key)
        event_overflow = len(events) > effective_limit
        observations = [
            {
                "cluster_id": str(row["cluster_id"]),
                "observed_ms": _epoch_ms(row["observed_at"]),
            }
            for row in observation_rows[:MAX_CHANGE_OBSERVATIONS]
        ]
        return {
            "events": events[:effective_limit],
            "observations": observations,
            "event_overflow": event_overflow,
            "observation_overflow": len(observation_rows) > MAX_CHANGE_OBSERVATIONS,
        }


def _inventory_events_statement(
    *,
    workspace_id: str,
    cluster_ids: set[str],
    allowed_application_ids: set[str],
    filters: ResourceFilters,
    start: datetime,
    end: datetime,
    limit: int,
) -> Select[Any]:
    if not _has_inventory_projection_filters(filters):
        return _bounded_inventory_events_statement(
            workspace_id=workspace_id,
            cluster_ids=cluster_ids,
            start=start,
            end=end,
            limit=limit,
        )

    event = TimelineLedgerEvent.__table__
    version = InventoryResourceVersion.__table__
    revision = InventoryFilterRevision.__table__
    from_clause = _inventory_correlation_join(
        event=event,
        revision=revision,
        version=version,
    )

    statement = (
        select(
            event.c.event_id,
            event.c.title,
            version.c.health,
            event.c.occurred_at,
        )
        .select_from(from_clause)
        .where(
            event.c.workspace_id == workspace_id,
            event.c.cluster_id.in_(cluster_ids),
            event.c.source == "inventory",
            event.c.activity == "change",
            event.c.event_type.in_(("add", "update", "delete")),
            event.c.occurred_at >= start,
            event.c.occurred_at < end,
        )
    )
    statement = _apply_inventory_filters(
        statement,
        version=version,
        filters=filters,
        allowed_application_ids=allowed_application_ids,
        labels_complete_column=revision.c.labels_complete,
        applications_complete_column=revision.c.application_bindings_complete,
    )
    return statement.order_by(event.c.occurred_at, event.c.event_id).limit(limit)


def _bounded_inventory_events_statement(
    *,
    workspace_id: str,
    cluster_ids: set[str],
    start: datetime,
    end: datetime,
    limit: int,
) -> Select[Any]:
    """Bound the ordered ledger candidates before exact projection lookups."""
    event = TimelineLedgerEvent.__table__
    per_cluster = tuple(
        _inventory_change_candidates_for_cluster(
            event=event,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            start=start,
            end=end,
            limit=limit,
        )
        for cluster_id in sorted(cluster_ids)
    )
    if len(per_cluster) == 1:
        candidates = per_cluster[0].subquery("bounded_inventory_change_events")
    else:
        merged = union_all(*per_cluster).subquery("per_cluster_inventory_change_events")
        candidates = (
            select(merged)
            .order_by(merged.c.occurred_at, merged.c.event_id)
            .limit(limit)
            .subquery("bounded_inventory_change_events")
        )
    version = InventoryResourceVersion.__table__
    revision = InventoryFilterRevision.__table__
    from_clause = _inventory_correlation_join(
        event=candidates,
        revision=revision,
        version=version,
    )
    return (
        select(
            candidates.c.event_id,
            candidates.c.title,
            version.c.health,
            candidates.c.occurred_at,
        )
        .select_from(from_clause)
        .order_by(candidates.c.occurred_at, candidates.c.event_id)
    )


def _inventory_change_candidates_for_cluster(
    *,
    event: Any,
    workspace_id: str,
    cluster_id: str,
    start: datetime,
    end: datetime,
    limit: int,
) -> Select[Any]:
    revision_id = _metadata_bigint(event.c.metadata, REVISION_ID_FIELD)
    version_id = _metadata_bigint(event.c.metadata, VERSION_ID_FIELD)
    source_snapshot_id = _metadata_text(event.c.metadata, SOURCE_SNAPSHOT_ID_FIELD)
    return (
        select(
            event.c.workspace_id,
            event.c.cluster_id,
            event.c.event_id,
            event.c.native_id,
            event.c.event_type,
            event.c.title,
            event.c.metadata,
            event.c.occurred_at,
        )
        .where(
            event.c.workspace_id == workspace_id,
            event.c.cluster_id == cluster_id,
            event.c.source == "inventory",
            event.c.activity == "change",
            event.c.event_type.in_(("add", "update", "delete")),
            event.c.occurred_at >= start,
            event.c.occurred_at < end,
            source_snapshot_id.is_not(None),
            revision_id.is_not(None),
            version_id.is_not(None),
        )
        .order_by(event.c.occurred_at, event.c.event_id)
        .limit(limit)
    )


def _inventory_correlation_join(*, event: Any, revision: Any, version: Any) -> Any:
    source_snapshot_id = _metadata_text(event.c.metadata, SOURCE_SNAPSHOT_ID_FIELD)
    revision_id = _metadata_bigint(event.c.metadata, REVISION_ID_FIELD)
    version_id = _metadata_bigint(event.c.metadata, VERSION_ID_FIELD)
    return event.join(
        revision,
        and_(
            revision.c.workspace_id == event.c.workspace_id,
            revision.c.cluster_id == event.c.cluster_id,
            revision.c.revision_id == revision_id,
            revision.c.snapshot_id == source_snapshot_id,
            revision.c.change_ledger_epoch == INVENTORY_CHANGE_LEDGER_EPOCH,
            revision.c.resources_complete.is_(True),
        ),
    ).join(
        version,
        and_(
            version.c.workspace_id == event.c.workspace_id,
            version.c.cluster_id == event.c.cluster_id,
            version.c.inventory_key == event.c.native_id,
            version.c.version_id == version_id,
            version.c.valid_from_revision <= revision.c.revision_id,
            or_(
                and_(
                    event.c.event_type == "delete",
                    version.c.valid_to_revision == revision.c.revision_id,
                ),
                and_(
                    event.c.event_type != "delete",
                    or_(
                        version.c.valid_to_revision.is_(None),
                        version.c.valid_to_revision > revision.c.revision_id,
                    ),
                ),
            ),
        ),
    )


def _has_inventory_projection_filters(filters: ResourceFilters) -> bool:
    return bool(
        filters.namespaces
        or filters.applications
        or filters.resource_types
        or filters.health
        or filters.labels
        or filters.query
    )


def _incident_events_statement(
    *,
    workspace_id: str,
    cluster_ids: set[str],
    allowed_application_ids: set[str],
    filters: ResourceFilters,
    start: datetime,
    end: datetime,
    limit: int,
) -> Select[Any]:
    timeline = RcaTimeline.__table__
    statement = select(
        timeline.c.id,
        timeline.c.incident_id,
        timeline.c.correlation_id,
        timeline.c.incident_resource_kind,
        timeline.c.incident_resource_name,
        timeline.c.incident_symptom,
        timeline.c.root_cause,
        timeline.c.severity,
        timeline.c.created_at,
    ).where(
        timeline.c.workspace_id == workspace_id,
        timeline.c.cluster_id.in_(cluster_ids),
        timeline.c.incident_id.is_not(None),
        timeline.c.created_at >= start,
        timeline.c.created_at < end,
    )
    statement = _apply_incident_filters(
        statement,
        timeline=timeline,
        filters=filters,
        allowed_application_ids=allowed_application_ids,
    )
    return statement.order_by(timeline.c.created_at, timeline.c.id).limit(limit)


def _deployment_events_statement(
    *,
    workspace_id: str,
    cluster_ids: set[str],
    application_ids: set[str],
    filters: ResourceFilters,
    start: datetime,
    end: datetime,
    limit: int,
) -> Select[Any]:
    run = WorkflowRun.__table__
    binding = DeploymentBinding.__table__
    source = run.join(
        binding,
        and_(
            binding.c.workspace_id == run.c.workspace_id,
            binding.c.binding_id == run.c.binding_id,
        ),
    )
    statement = (
        select(
            run.c.workflow_run_id,
            run.c.application_id,
            run.c.status,
            run.c.summary,
            run.c.updated_at,
        )
        .select_from(source)
        .where(
            run.c.workspace_id == workspace_id,
            run.c.cluster_id.in_(cluster_ids),
            run.c.application_id.in_(application_ids),
            run.c.updated_at >= start,
            run.c.updated_at < end,
        )
    )
    statement = _apply_deployment_filters(
        statement,
        run=run,
        binding=binding,
        filters=filters,
    )
    return statement.order_by(run.c.updated_at, run.c.workflow_run_id).limit(limit)


def _observations_statement(
    *,
    workspace_id: str,
    cluster_ids: set[str],
    filters: ResourceFilters,
    start: datetime,
    end: datetime,
    limit: int,
) -> Select[Any]:
    revision = InventoryFilterRevision.__table__
    statement = (
        select(revision.c.cluster_id, revision.c.observed_at)
        .where(
            revision.c.workspace_id == workspace_id,
            revision.c.cluster_id.in_(cluster_ids),
            revision.c.change_ledger_epoch == INVENTORY_CHANGE_LEDGER_EPOCH,
            revision.c.resources_complete.is_(True),
            revision.c.observed_at >= start,
            revision.c.observed_at < end,
        )
        .order_by(revision.c.observed_at, revision.c.revision_id)
        .limit(limit)
    )
    if filters.labels:
        statement = statement.where(revision.c.labels_complete.is_(True))
    if filters.applications:
        statement = statement.where(revision.c.application_bindings_complete.is_(True))
    return statement


def _apply_inventory_filters(
    statement: Select[Any],
    *,
    version: Any,
    filters: ResourceFilters,
    allowed_application_ids: set[str],
    labels_complete_column: Any | None = None,
    applications_complete_column: Any | None = None,
) -> Select[Any]:
    if filters.namespaces:
        statement = statement.where(_namespace_clause(version, filters.namespaces))
    if filters.resource_types:
        statement = statement.where(version.c.resource_type.in_(filters.resource_types))
    if filters.health:
        statement = statement.where(func.lower(version.c.health).in_(filters.health))
    if filters.labels:
        if labels_complete_column is None:
            return statement.where(false())
        label = InventoryResourceLabelVersion.__table__
        statement = statement.where(
            labels_complete_column.is_(True),
            *(
                select(literal(1))
                .select_from(label)
                .where(
                    label.c.workspace_id == version.c.workspace_id,
                    label.c.cluster_id == version.c.cluster_id,
                    label.c.version_id == version.c.version_id,
                    label.c.key == key,
                    label.c.value == value,
                )
                .exists()
                for key, value in filters.labels
            ),
        )
    if filters.applications:
        requested = set(filters.applications)
        visible = requested & allowed_application_ids
        if requested - allowed_application_ids or not visible:
            return statement.where(false())
        if applications_complete_column is None:
            return statement.where(false())
        binding = InventoryResourceApplicationVersion.__table__
        statement = statement.where(
            applications_complete_column.is_(True),
            version.c.application_binding_complete.is_(True),
            select(literal(1))
            .select_from(binding)
            .where(
                binding.c.workspace_id == version.c.workspace_id,
                binding.c.version_id == version.c.version_id,
                binding.c.application_id.in_(visible),
            )
            .exists(),
        )
    if filters.query:
        statement = statement.where(
            func.lower(version.c.search_text).like(
                f"%{_escape_like(filters.query.casefold())}%",
                escape="\\",
            )
        )
    return statement


def _apply_incident_filters(
    statement: Select[Any],
    *,
    timeline: Any,
    filters: ResourceFilters,
    allowed_application_ids: set[str],
) -> Select[Any]:
    if filters.namespaces:
        statement = statement.where(
            _namespace_clause(
                timeline,
                filters.namespaces,
                namespace_column="incident_namespace",
            )
        )
    if filters.resource_types:
        kinds = set(filters.resource_types)
        if "workload" in kinds:
            kinds.update(WORKLOAD_KINDS)
        statement = statement.where(
            func.lower(func.coalesce(timeline.c.incident_resource_kind, "")).in_(kinds)
        )
    if filters.health:
        statement = statement.where(
            func.lower(func.coalesce(timeline.c.severity, "unknown")).in_(
                _source_values_for_health(filters.health)
            )
        )
    if filters.labels:
        statement = statement.where(
            timeline.c.labels_complete.is_(True),
            *(timeline.c.labels.contains({key: value}) for key, value in filters.labels),
        )
    if filters.applications:
        requested = set(filters.applications)
        visible = requested & allowed_application_ids
        if requested - allowed_application_ids or not visible:
            return statement.where(false())
        statement = statement.where(
            timeline.c.application_ids_complete.is_(True),
            or_(*(timeline.c.application_ids.contains([value]) for value in sorted(visible))),
        )
    if filters.query:
        pattern = f"%{_escape_like(filters.query.casefold())}%"
        statement = statement.where(
            or_(
                func.lower(func.coalesce(timeline.c.incident_resource_name, "")).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(func.coalesce(timeline.c.incident_symptom, "")).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(func.coalesce(timeline.c.root_cause, "")).like(
                    pattern,
                    escape="\\",
                ),
            )
        )
    return statement


def _apply_deployment_filters(
    statement: Select[Any],
    *,
    run: Any,
    binding: Any,
    filters: ResourceFilters,
) -> Select[Any]:
    if filters.namespaces:
        statement = statement.where(_namespace_clause(binding, filters.namespaces))
    if filters.resource_types and not set(filters.resource_types) & {
        "application",
        "deployment",
        "workload",
    }:
        return statement.where(false())
    if filters.labels:
        return statement.where(false())
    if filters.health:
        statement = statement.where(
            func.lower(run.c.status).in_(_source_values_for_health(filters.health))
        )
    if filters.query:
        pattern = f"%{_escape_like(filters.query.casefold())}%"
        statement = statement.where(
            or_(
                func.lower(run.c.application_id).like(pattern, escape="\\"),
                func.lower(run.c.status).like(pattern, escape="\\"),
                func.lower(func.coalesce(run.c.summary, "")).like(pattern, escape="\\"),
            )
        )
    return statement


def _namespace_clause(
    table: Any,
    namespaces: tuple[tuple[str, str], ...],
    *,
    namespace_column: str = "namespace",
) -> Any:
    column = getattr(table.c, namespace_column)
    return or_(
        *(
            and_(table.c.cluster_id == cluster_id, column == namespace)
            for cluster_id, namespace in namespaces
        )
    )


def _inventory_event(row: Mapping[str, Any]) -> JsonObject:
    return {
        "id": str(row["event_id"]),
        "kind": "inventory_event",
        "occurredMs": _epoch_ms(row["occurred_at"]),
        "title": _title(str(row["title"])),
        "severity": normalize_change_severity(row.get("health")),
    }


def _incident_event(row: Mapping[str, Any]) -> JsonObject:
    event_id = str(row.get("incident_id") or row.get("correlation_id") or row["id"])
    title = str(row.get("incident_symptom") or "").strip()
    if not title:
        resource = str(row.get("incident_resource_name") or "").strip()
        title = f"Incident · {resource}" if resource else f"Incident {event_id}"
    return {
        "id": f"incident:{event_id}",
        "kind": "incident",
        "occurredMs": _epoch_ms(row["created_at"]),
        "title": _title(title),
        "severity": normalize_change_severity(row.get("severity")),
    }


def _deployment_event(row: Mapping[str, Any]) -> JsonObject:
    status = str(row.get("status") or "unknown")
    title = str(row.get("summary") or "").strip()
    if not title:
        title = f"Deployment {str(row.get('application_id') or 'unknown')} {status}"
    return {
        "id": f"deployment:{row['workflow_run_id']}",
        "kind": "deployment",
        "occurredMs": _epoch_ms(row["updated_at"]),
        "title": _title(title),
        "severity": normalize_change_severity(status),
    }


def normalize_change_severity(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in CRITICAL_VALUES:
        return "critical"
    if normalized in WARNING_VALUES:
        return "warning"
    if normalized in INFO_VALUES:
        return "info"
    return "unknown"


def _source_values_for_health(health: Collection[str]) -> set[str]:
    values: set[str] = set()
    for value in health:
        normalized = str(value).casefold()
        values.add(normalized)
        severity = normalize_change_severity(normalized)
        if severity == "critical":
            values.update(CRITICAL_VALUES)
        elif severity == "warning":
            values.update(WARNING_VALUES)
        elif severity == "info":
            values.update(INFO_VALUES)
        else:
            values.add("unknown")
    return values


def _selected_clusters(filters: ResourceFilters, allowed_cluster_ids: Collection[str]) -> set[str]:
    allowed = _ids(allowed_cluster_ids)
    selected = set(filters.clusters) | {cluster_id for cluster_id, _namespace in filters.namespaces}
    return (selected if selected else allowed) & allowed


def _ids(values: Collection[str]) -> set[str]:
    return {str(value).strip() for value in values if str(value).strip()}


def _execute_rows(conn: Any, statement: Select[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(statement).mappings().all()]


def _datetime_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000, tz=UTC)


def _epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1_000)


def _event_sort_key(event: Mapping[str, Any]) -> tuple[int, str, str]:
    return int(event["occurredMs"]), str(event["kind"]), str(event["id"])


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _title(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized[:240] or "Observed change"


def _metadata_text(metadata: Any, field: str) -> Any:
    value = metadata[field]
    return case(
        (func.jsonb_typeof(value) == "string", value.astext),
        else_=None,
    )


def _metadata_bigint(metadata: Any, field: str) -> Any:
    value = metadata[field]
    return case(
        (func.jsonb_typeof(value) == "number", cast(value.astext, BigInteger)),
        else_=None,
    )
