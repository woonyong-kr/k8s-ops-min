"""Read-only product projections for the Applications surface."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import and_, func, or_, select, true

from domains.dashboard.models import RcaTimeline
from domains.dashboard.repository import OPEN_INCIDENT_STATUSES
from domains.gitops.models import Application, DeploymentBinding, WorkflowRun, WorkflowRunStep
from domains.gitops.repository import serialize_workflow_run
from domains.inventory_filter.models import (
    InventoryResourceApplicationVersion,
    InventoryResourceVersion,
)
from domains.inventory_filter.repository import (
    InventoryFilterRepository,
    aggregate_snapshot_contexts,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection, iso_or_none

APPLICATION_CATALOG_MAX_APPLICATIONS = 200
APPLICATION_CATALOG_BINDING_LIMIT = 500
APPLICATION_CATALOG_WORKFLOW_LIMIT = 100
APPLICATION_CATALOG_INCIDENT_LIMIT = 3


class ApplicationsProductRepository(DatabaseConnection):
    """Queries only allowlisted evidence needed by BQ-039 through BQ-042."""

    def get_application_inventory_evidence(
        self,
        *,
        workspace_id: str,
        application_id: str,
        allowed_cluster_ids: Collection[str],
    ) -> list[JsonObject]:
        cluster_ids = _ids(allowed_cluster_ids)
        if not workspace_id or not application_id or not cluster_ids:
            return []
        resource = InventoryResourceVersion.__table__
        application = InventoryResourceApplicationVersion.__table__
        statement = (
            select(
                resource.c.inventory_key,
                resource.c.cluster_id,
                resource.c.resource_type,
                resource.c.api_version,
                resource.c.kind,
                resource.c.namespace,
                resource.c.name,
                resource.c.uid,
                resource.c.status,
                resource.c.health,
                resource.c.labels,
                resource.c.summary,
                resource.c.application_binding_complete,
                resource.c.observed_at,
            )
            .select_from(
                resource.join(
                    application,
                    and_(
                        application.c.version_id == resource.c.version_id,
                        application.c.workspace_id == resource.c.workspace_id,
                        application.c.cluster_id == resource.c.cluster_id,
                    ),
                )
            )
            .where(
                resource.c.workspace_id == workspace_id,
                resource.c.cluster_id.in_(cluster_ids),
                resource.c.valid_to_revision.is_(None),
                application.c.application_id == application_id,
            )
            .order_by(
                resource.c.cluster_id,
                resource.c.resource_type,
                resource.c.kind,
                resource.c.namespace,
                resource.c.name,
                resource.c.inventory_key,
            )
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_inventory_evidence_row(row) for row in rows]

    def get_application_catalog_states(
        self,
        *,
        workspace_id: str,
        application_ids: Collection[str],
        allowed_cluster_ids: Collection[str],
    ) -> dict[str, JsonObject]:
        """Read list-card evidence for at most 200 authorized applications in fixed queries."""
        applications = _ids(application_ids)
        cluster_ids = _ids(allowed_cluster_ids)
        if len(applications) > APPLICATION_CATALOG_MAX_APPLICATIONS:
            raise ValueError(
                f"application catalog batch exceeds {APPLICATION_CATALOG_MAX_APPLICATIONS}"
            )
        states = {application_id: _empty_catalog_state() for application_id in applications}
        if not workspace_id or not applications or not cluster_ids:
            return states

        with self.connection() as conn:
            binding_rows = conn.execute(
                _catalog_bindings_statement(workspace_id, applications, cluster_ids)
            ).mappings()
            for row in binding_rows:
                states[str(row["application_id"])]["bindings"].append(dict(row))

            run_rows = [
                dict(row)
                for row in conn.execute(
                    _catalog_runs_statement(workspace_id, applications, cluster_ids)
                )
                .mappings()
                .all()
            ]
            run_ids = tuple(str(row["workflow_run_id"]) for row in run_rows)
            step_rows = conn.execute(_catalog_run_steps_statement(workspace_id, run_ids)).mappings()
            steps_by_run: dict[str, list[JsonObject]] = defaultdict(list)
            for step in step_rows:
                steps_by_run[str(step["workflow_run_id"])].append(
                    {
                        "name": step["name"],
                        "status": step["status"],
                        "message": step["message"],
                        "details": dict(step["details"] or {}),
                        "updated_at": iso_or_none(step["updated_at"]),
                    }
                )
            for row in run_rows:
                run = serialize_workflow_run(row)
                run["steps"] = steps_by_run.get(str(run["workflow_run_id"]), [])
                states[str(run["application_id"])]["runs"].append(run)

            inventory_rows = conn.execute(
                _catalog_inventory_statement(workspace_id, applications, cluster_ids)
            ).mappings()
            for row in inventory_rows:
                states[str(row["application_id"])]["inventory_rows"].append(
                    _inventory_evidence_row(row)
                )

            incident_rows = conn.execute(
                _catalog_incidents_statement(workspace_id, applications, cluster_ids)
            ).mappings()
            incident_open_counts: dict[str, int] = {}
            for row in incident_rows:
                application_id = str(row["application_id"])
                states[application_id]["incident_evidence"]["items"].append(
                    _incident_evidence_item(row)
                )
                incident_open_counts[application_id] = int(row["open_count"])

            incident_incomplete = (
                int(
                    conn.execute(
                        _catalog_incident_incomplete_statement(workspace_id, cluster_ids)
                    ).scalar_one()
                )
                > 0
            )
            cluster_contexts = InventoryFilterRepository.filter_snapshot_contexts(
                self,
                workspace_id,
                cluster_ids,
            )

        allowed_clusters = set(cluster_ids)
        for application_id, state in states.items():
            bound_cluster_ids = {
                str(binding.get("cluster_id") or "")
                for binding in state["bindings"]
                if str(binding.get("cluster_id") or "")
            }
            evidence_cluster_ids = bound_cluster_ids or allowed_clusters
            state["inventory_context"] = aggregate_snapshot_contexts(
                cluster_contexts,
                evidence_cluster_ids,
            )
            state["incident_evidence"]["complete"] = not incident_incomplete
            state["incident_evidence"]["open_count"] = (
                None if incident_incomplete else incident_open_counts.get(application_id, 0)
            )
        return states

    def get_application_incident_evidence(
        self,
        *,
        workspace_id: str,
        application_id: str,
        allowed_cluster_ids: Collection[str],
        limit: int = 3,
    ) -> JsonObject:
        cluster_ids = _ids(allowed_cluster_ids)
        if not workspace_id or not application_id or not cluster_ids:
            return {"complete": False, "open_count": None, "items": []}
        table = RcaTimeline.__table__
        scope = (
            table.c.workspace_id == workspace_id,
            table.c.cluster_id.in_(cluster_ids),
            table.c.incident_id.is_not(None),
        )
        exact_application = and_(
            table.c.application_ids_complete.is_(True),
            table.c.application_ids.contains([application_id]),
        )
        item_statement = (
            select(
                table.c.incident_id,
                table.c.correlation_id,
                table.c.incident_symptom,
                table.c.root_cause,
                table.c.status,
                table.c.created_at,
                table.c.updated_at,
            )
            .where(*scope, exact_application)
            .order_by(table.c.updated_at.desc(), table.c.id.desc())
            .limit(max(1, min(limit, 3)))
        )
        open_count_statement = (
            select(func.count())
            .select_from(table)
            .where(
                *scope,
                exact_application,
                table.c.status.in_(OPEN_INCIDENT_STATUSES),
            )
        )
        incomplete_statement = (
            select(func.count())
            .select_from(table)
            .where(
                *scope,
                table.c.application_ids_complete.is_(False),
            )
        )
        with self.connection() as conn:
            rows = conn.execute(item_statement).mappings().all()
            incomplete = int(conn.execute(incomplete_statement).scalar_one()) > 0
            open_count = None
            if not incomplete:
                open_count = int(conn.execute(open_count_statement).scalar_one())
        return {
            "complete": not incomplete,
            "open_count": open_count,
            "items": [
                {
                    "id": str(row.get("incident_id") or row["correlation_id"]),
                    "title": _optional_text(row.get("incident_symptom") or row.get("root_cause")),
                    "status": str(row["status"]),
                    "started_at": iso_or_none(row.get("created_at")),
                    "updated_at": iso_or_none(row.get("updated_at")),
                }
                for row in rows
            ],
        }

    def get_application_workload_runtime_evidence(
        self,
        *,
        workspace_id: str,
        cluster_id: str,
        namespace: str | None,
        pod_limit: int,
    ) -> JsonObject:
        """Read bounded runtime neighbors for one already-authorized workload root.

        The caller obtains the root from the direct rendered-manifest binding
        first.  This method intentionally returns only current Pods in that
        root's namespace and their explicitly named Nodes.  Relationship
        membership is decided later by the graph module from owner UID or a
        structured selector; no SQL label/name approximation makes a Pod part
        of an application or workload.
        """

        if not workspace_id or not cluster_id or pod_limit < 1:
            return {"rows": [], "truncated": False}
        resource = InventoryResourceVersion.__table__
        pod_statement = (
            select(
                resource.c.inventory_key,
                resource.c.cluster_id,
                resource.c.resource_type,
                resource.c.api_version,
                resource.c.kind,
                resource.c.namespace,
                resource.c.name,
                resource.c.uid,
                resource.c.status,
                resource.c.health,
                resource.c.labels,
                resource.c.summary,
                resource.c.observed_at,
            )
            .where(
                resource.c.workspace_id == workspace_id,
                resource.c.cluster_id == cluster_id,
                resource.c.valid_to_revision.is_(None),
                resource.c.resource_type == "pod",
                resource.c.namespace == namespace,
            )
            .order_by(resource.c.name, resource.c.inventory_key)
            .limit(pod_limit + 1)
        )
        with self.connection() as conn:
            pod_records = conn.execute(pod_statement).mappings().all()
            truncated = len(pod_records) > pod_limit
            pod_records = pod_records[:pod_limit]
            node_names = sorted(
                {
                    str(dict(row.get("summary") or {}).get("node_name") or "")
                    for row in pod_records
                    if str(dict(row.get("summary") or {}).get("node_name") or "")
                }
            )
            node_records = []
            if node_names:
                node_records = (
                    conn.execute(
                        select(
                            resource.c.inventory_key,
                            resource.c.cluster_id,
                            resource.c.resource_type,
                            resource.c.api_version,
                            resource.c.kind,
                            resource.c.namespace,
                            resource.c.name,
                            resource.c.uid,
                            resource.c.status,
                            resource.c.health,
                            resource.c.labels,
                            resource.c.summary,
                            resource.c.observed_at,
                        )
                        .where(
                            resource.c.workspace_id == workspace_id,
                            resource.c.cluster_id == cluster_id,
                            resource.c.valid_to_revision.is_(None),
                            resource.c.resource_type == "node",
                            resource.c.name.in_(node_names),
                        )
                        .order_by(resource.c.name, resource.c.inventory_key)
                    )
                    .mappings()
                    .all()
                )
        return {
            "rows": [_runtime_evidence_row(row) for row in [*pod_records, *node_records]],
            "truncated": truncated,
        }


def _catalog_bindings_statement(
    workspace_id: str,
    application_ids: tuple[str, ...],
    cluster_ids: tuple[str, ...],
) -> Any:
    application = Application.__table__
    binding = DeploymentBinding.__table__
    ranked = (
        select(
            application.c.application_id,
            binding.c.binding_id,
            binding.c.cluster_id,
            binding.c.namespace,
            binding.c.environment,
            binding.c.status,
            func.row_number()
            .over(
                partition_by=application.c.application_id,
                order_by=(
                    binding.c.environment,
                    binding.c.cluster_id,
                    binding.c.namespace,
                    binding.c.binding_id,
                ),
            )
            .label("rank"),
        )
        .select_from(
            application.join(
                binding,
                and_(
                    binding.c.workspace_id == application.c.workspace_id,
                    binding.c.repository_id == application.c.repository_id,
                    or_(
                        binding.c.app_name == application.c.name,
                        and_(
                            application.c.manifest_path != "",
                            binding.c.manifest_path == application.c.manifest_path,
                        ),
                    ),
                ),
            )
        )
        .where(
            application.c.workspace_id == workspace_id,
            application.c.application_id.in_(application_ids),
            binding.c.cluster_id.in_(cluster_ids),
        )
        .cte("ranked_application_catalog_bindings")
    )
    return (
        select(
            ranked.c.application_id,
            ranked.c.binding_id,
            ranked.c.cluster_id,
            ranked.c.namespace,
            ranked.c.environment,
            ranked.c.status,
        )
        .where(ranked.c.rank <= APPLICATION_CATALOG_BINDING_LIMIT)
        .order_by(ranked.c.application_id, ranked.c.rank)
    )


def _catalog_runs_statement(
    workspace_id: str,
    application_ids: tuple[str, ...],
    cluster_ids: tuple[str, ...],
) -> Any:
    table = WorkflowRun.__table__
    ranked = (
        select(
            *table.c,
            func.row_number()
            .over(
                partition_by=table.c.application_id,
                order_by=(table.c.created_at.desc(), table.c.workflow_run_id.desc()),
            )
            .label("rank"),
        )
        .where(
            table.c.workspace_id == workspace_id,
            table.c.application_id.in_(application_ids),
        )
        .cte("ranked_application_catalog_runs")
    )
    return (
        select(*(ranked.c[column.name] for column in table.columns))
        .where(
            ranked.c.rank <= APPLICATION_CATALOG_WORKFLOW_LIMIT,
            ranked.c.cluster_id.in_(cluster_ids),
        )
        .order_by(ranked.c.application_id, ranked.c.rank)
    )


def _catalog_run_steps_statement(workspace_id: str, run_ids: tuple[str, ...]) -> Any:
    table = WorkflowRunStep.__table__
    return (
        select(
            table.c.workflow_run_id,
            table.c.name,
            table.c.status,
            table.c.message,
            table.c.details,
            table.c.updated_at,
        )
        .where(
            table.c.workspace_id == workspace_id,
            table.c.workflow_run_id.in_(run_ids),
        )
        .order_by(table.c.workflow_run_id, table.c.created_at)
    )


def _catalog_inventory_statement(
    workspace_id: str,
    application_ids: tuple[str, ...],
    cluster_ids: tuple[str, ...],
) -> Any:
    resource = InventoryResourceVersion.__table__
    application = InventoryResourceApplicationVersion.__table__
    return (
        select(
            application.c.application_id,
            resource.c.inventory_key,
            resource.c.cluster_id,
            resource.c.resource_type,
            resource.c.api_version,
            resource.c.kind,
            resource.c.namespace,
            resource.c.name,
            resource.c.uid,
            resource.c.status,
            resource.c.health,
            resource.c.labels,
            resource.c.summary,
            resource.c.application_binding_complete,
            resource.c.observed_at,
        )
        .select_from(
            resource.join(
                application,
                and_(
                    application.c.version_id == resource.c.version_id,
                    application.c.workspace_id == resource.c.workspace_id,
                    application.c.cluster_id == resource.c.cluster_id,
                ),
            )
        )
        .where(
            resource.c.workspace_id == workspace_id,
            resource.c.cluster_id.in_(cluster_ids),
            resource.c.valid_to_revision.is_(None),
            application.c.application_id.in_(application_ids),
        )
        .order_by(
            application.c.application_id,
            resource.c.cluster_id,
            resource.c.resource_type,
            resource.c.kind,
            resource.c.namespace,
            resource.c.name,
            resource.c.inventory_key,
        )
    )


def _catalog_incidents_statement(
    workspace_id: str,
    application_ids: tuple[str, ...],
    cluster_ids: tuple[str, ...],
) -> Any:
    table = RcaTimeline.__table__
    expanded = (
        func.jsonb_array_elements_text(table.c.application_ids)
        .table_valued("application_id")
        .render_derived()
        .lateral("incident_application")
    )
    ranked = (
        select(
            expanded.c.application_id,
            table.c.incident_id,
            table.c.correlation_id,
            table.c.incident_symptom,
            table.c.root_cause,
            table.c.status,
            table.c.created_at,
            table.c.updated_at,
            func.count()
            .filter(table.c.status.in_(OPEN_INCIDENT_STATUSES))
            .over(partition_by=expanded.c.application_id)
            .label("open_count"),
            func.row_number()
            .over(
                partition_by=expanded.c.application_id,
                order_by=(table.c.updated_at.desc(), table.c.id.desc()),
            )
            .label("rank"),
        )
        .select_from(table.join(expanded, true()))
        .where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id.in_(cluster_ids),
            table.c.incident_id.is_not(None),
            table.c.application_ids_complete.is_(True),
            expanded.c.application_id.in_(application_ids),
        )
        .cte("ranked_application_catalog_incidents")
    )
    return (
        select(ranked)
        .where(ranked.c.rank <= APPLICATION_CATALOG_INCIDENT_LIMIT)
        .order_by(ranked.c.application_id, ranked.c.rank)
    )


def _catalog_incident_incomplete_statement(
    workspace_id: str,
    cluster_ids: tuple[str, ...],
) -> Any:
    table = RcaTimeline.__table__
    return (
        select(func.count())
        .select_from(table)
        .where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id.in_(cluster_ids),
            table.c.incident_id.is_not(None),
            table.c.application_ids_complete.is_(False),
        )
    )


def _ids(values: Collection[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _runtime_evidence_row(row: Any) -> JsonObject:
    return {
        "id": str(row["inventory_key"]),
        "cluster_id": str(row["cluster_id"]),
        "resource_type": str(row["resource_type"]),
        "api_version": str(row["api_version"]),
        "kind": str(row["kind"]),
        "namespace": _optional_text(row.get("namespace")),
        "name": str(row["name"]),
        "uid": _optional_text(row.get("uid")),
        "status": str(row["status"]),
        "health": str(row["health"]),
        "labels": dict(row.get("labels") or {}),
        "summary": dict(row.get("summary") or {}),
        "observed_at": iso_or_none(row.get("observed_at")),
    }


def _inventory_evidence_row(row: Mapping[str, Any]) -> JsonObject:
    return {
        "id": str(row["inventory_key"]),
        "cluster_id": str(row["cluster_id"]),
        "resource_type": str(row["resource_type"]),
        "api_version": str(row["api_version"]),
        "kind": str(row["kind"]),
        "namespace": str(row["namespace"]) if row.get("namespace") is not None else None,
        "name": str(row["name"]),
        "uid": _optional_text(row.get("uid")),
        "status": str(row["status"]),
        "health": str(row["health"]),
        "labels": dict(row.get("labels") or {}),
        "summary": dict(row.get("summary") or {}),
        "binding_complete": bool(row["application_binding_complete"]),
        "observed_at": iso_or_none(row.get("observed_at")),
    }


def _incident_evidence_item(row: Mapping[str, Any]) -> JsonObject:
    return {
        "id": str(row.get("incident_id") or row["correlation_id"]),
        "title": _optional_text(row.get("incident_symptom") or row.get("root_cause")),
        "status": str(row["status"]),
        "started_at": iso_or_none(row.get("created_at")),
        "updated_at": iso_or_none(row.get("updated_at")),
    }


def _empty_catalog_state() -> JsonObject:
    return {
        "bindings": [],
        "runs": [],
        "inventory_rows": [],
        "inventory_context": {
            "snapshot_revision": 0,
            "observed_at": None,
            "labels_complete": True,
            "resources_complete": True,
            "application_bindings_complete": True,
            "partial_reason_codes": [],
        },
        "incident_evidence": {
            "complete": False,
            "open_count": None,
            "items": [],
        },
    }
