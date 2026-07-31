"""Bounded GitOps fleet aggregate with no application- or resource-level N+1 reads."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from typing import Any

from sqlalchemy import and_, func, literal, or_, select

from domains.gitops.models import Application, DeploymentBinding, GitWatchTarget
from domains.gitops.overview_projection import GITOPS_RESOURCE_FAMILIES
from domains.gitops.overview_query import GitOpsOverviewFilters
from domains.inventory_filter.models import (
    InventoryResourceApplicationVersion,
    InventoryResourceLabelVersion,
)
from domains.inventory_filter.repository import current_inventory_versions
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection

MAX_GITOPS_OVERVIEW_ROWS = 500


class GitOpsOverviewRepository(DatabaseConnection):
    def list_gitops_overview(
        self,
        *,
        workspace_id: str,
        allowed_cluster_ids: Collection[str],
        allowed_application_ids: Collection[str],
        filters: GitOpsOverviewFilters,
        snapshot_revision: int,
        limit: int,
    ) -> JsonObject:
        cluster_ids = tuple(sorted(set(allowed_cluster_ids)))
        application_ids = tuple(sorted(set(allowed_application_ids)))
        effective_limit = max(1, min(limit, MAX_GITOPS_OVERVIEW_ROWS))
        if not workspace_id or not cluster_ids:
            return {"registered_rows": [], "inventory_rows": [], "has_more": False}
        requested_clusters = tuple(
            cluster_id
            for cluster_id in cluster_ids
            if not filters.clusters or cluster_id in filters.clusters
        )
        if not requested_clusters:
            return {"registered_rows": [], "inventory_rows": [], "has_more": False}

        registered_statement = _registered_statement(
            workspace_id=workspace_id,
            cluster_ids=requested_clusters,
            application_ids=application_ids,
            filters=filters,
            limit=effective_limit + 1,
        )
        inventory_statement = _inventory_statement(
            workspace_id=workspace_id,
            cluster_ids=requested_clusters,
            filters=filters,
            snapshot_revision=snapshot_revision,
            limit=effective_limit + 1,
        )
        with self.connection() as conn:
            registered_rows = (
                [dict(row) for row in conn.execute(registered_statement).mappings().all()]
                if registered_statement is not None
                else []
            )
            inventory_rows = (
                [dict(row) for row in conn.execute(inventory_statement).mappings().all()]
                if inventory_statement is not None
                else []
            )
            version_ids = [int(row["version_id"]) for row in inventory_rows]
            applications_by_version = _applications_for_versions(
                conn,
                workspace_id=workspace_id,
                version_ids=version_ids,
                allowed_application_ids=application_ids,
            )

        has_more = len(registered_rows) + len(inventory_rows) > effective_limit
        combined: list[tuple[str, JsonObject]] = [("controller", row) for row in inventory_rows] + [
            ("registered", row) for row in registered_rows
        ]
        combined.sort(
            key=lambda item: (
                0 if item[0] == "controller" else 1,
                str(item[1].get("name") or item[1].get("application_name") or ""),
                str(item[1].get("uid") or item[1].get("binding_id") or ""),
            )
        )
        selected = combined[:effective_limit]
        selected_registered = [row for authority, row in selected if authority == "registered"]
        selected_inventory = [row for authority, row in selected if authority == "controller"]
        for row in selected_inventory:
            row["application_ids"] = list(applications_by_version.get(int(row["version_id"]), ()))
        return {
            "registered_rows": selected_registered,
            "inventory_rows": selected_inventory,
            "has_more": has_more,
        }


def _registered_statement(
    *,
    workspace_id: str,
    cluster_ids: tuple[str, ...],
    application_ids: tuple[str, ...],
    filters: GitOpsOverviewFilters,
    limit: int,
) -> Any | None:
    if not application_ids or (filters.providers and "internal" not in filters.providers):
        return None
    application = Application.__table__
    binding = DeploymentBinding.__table__
    watch = GitWatchTarget.__table__
    statement = (
        select(
            application.c.application_id,
            application.c.name.label("application_name"),
            binding.c.binding_id,
            binding.c.cluster_id,
            binding.c.namespace,
            binding.c.environment,
            binding.c.status,
            watch.c.last_seen_commit_sha.label("revision"),
            watch.c.last_polled_at.label("observed_at"),
        )
        .select_from(
            binding.join(
                application,
                and_(
                    application.c.workspace_id == binding.c.workspace_id,
                    application.c.repository_id == binding.c.repository_id,
                    application.c.name == binding.c.app_name,
                    application.c.manifest_path == binding.c.manifest_path,
                ),
            ).outerjoin(
                watch,
                and_(
                    watch.c.workspace_id == binding.c.workspace_id,
                    watch.c.repository_id == binding.c.repository_id,
                    watch.c.watch_target_id == binding.c.watch_target_id,
                ),
            )
        )
        .where(
            binding.c.workspace_id == workspace_id,
            binding.c.cluster_id.in_(cluster_ids),
            application.c.application_id.in_(application_ids),
        )
    )
    if filters.applications:
        statement = statement.where(application.c.application_id.in_(filters.applications))
    if filters.namespaces:
        statement = statement.where(
            or_(
                *(
                    and_(binding.c.cluster_id == cluster_id, binding.c.namespace == namespace)
                    for cluster_id, namespace in filters.namespaces
                )
            )
        )
    if filters.query:
        pattern = f"%{_escape_like(filters.query.casefold())}%"
        statement = statement.where(
            or_(
                func.lower(application.c.name).like(pattern, escape="\\"),
                func.lower(binding.c.environment).like(pattern, escape="\\"),
                func.lower(binding.c.namespace).like(pattern, escape="\\"),
            )
        )
    return statement.order_by(application.c.name, binding.c.binding_id).limit(limit)


def _inventory_statement(
    *,
    workspace_id: str,
    cluster_ids: tuple[str, ...],
    filters: GitOpsOverviewFilters,
    snapshot_revision: int,
    limit: int,
) -> Any | None:
    if snapshot_revision <= 0 or (
        filters.providers and not {"argo", "flux"}.intersection(filters.providers)
    ):
        return None
    current = current_inventory_versions(
        workspace_id,
        cluster_ids,
        snapshot_revision,
        include_deleted=False,
    )
    supported = tuple(
        and_(
            func.lower(current.c.api_version).like(f"{group}/%"),
            func.lower(current.c.kind) == kind,
        )
        for group, kind in GITOPS_RESOURCE_FAMILIES
    )
    statement = select(current).where(current.c.rank == 1, or_(*supported))
    if filters.namespaces:
        statement = statement.where(
            or_(
                *(
                    and_(current.c.cluster_id == cluster_id, current.c.namespace == namespace)
                    for cluster_id, namespace in filters.namespaces
                )
            )
        )
    if filters.kinds:
        statement = statement.where(func.lower(current.c.kind).in_(filters.kinds))
    if filters.providers:
        provider_groups = {
            group
            for (group, _kind), (provider, _role) in GITOPS_RESOURCE_FAMILIES.items()
            if provider in filters.providers
        }
        statement = statement.where(
            or_(
                *(func.lower(current.c.api_version).like(f"{group}/%") for group in provider_groups)
            )
        )
    if filters.query:
        statement = statement.where(
            current.c.search_text.like(
                f"%{_escape_like(filters.query.casefold())}%",
                escape="\\",
            )
        )
    for index, (key, value) in enumerate(filters.labels):
        label = InventoryResourceLabelVersion.__table__.alias(f"gitops_overview_label_{index}")
        statement = statement.where(
            select(literal(1))
            .select_from(label)
            .where(
                label.c.version_id == current.c.version_id,
                label.c.workspace_id == current.c.workspace_id,
                label.c.key == key,
                label.c.value == value,
            )
            .exists()
        )
    if filters.applications:
        application = InventoryResourceApplicationVersion.__table__
        statement = statement.where(
            select(literal(1))
            .select_from(application)
            .where(
                application.c.version_id == current.c.version_id,
                application.c.workspace_id == current.c.workspace_id,
                application.c.application_id.in_(filters.applications),
            )
            .exists()
        )
    return statement.order_by(
        current.c.cluster_id,
        current.c.kind,
        current.c.namespace,
        current.c.name,
        current.c.version_id,
    ).limit(limit)


def _applications_for_versions(
    conn: Any,
    *,
    workspace_id: str,
    version_ids: list[int],
    allowed_application_ids: tuple[str, ...],
) -> dict[int, tuple[str, ...]]:
    if not version_ids or not allowed_application_ids:
        return {}
    table = InventoryResourceApplicationVersion.__table__
    rows = conn.execute(
        select(table.c.version_id, table.c.application_id).where(
            table.c.workspace_id == workspace_id,
            table.c.version_id.in_(version_ids),
            table.c.application_id.in_(allowed_application_ids),
        )
    ).mappings()
    values: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        values[int(row["version_id"])].append(str(row["application_id"]))
    return {key: tuple(sorted(set(items))) for key, items in values.items()}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
