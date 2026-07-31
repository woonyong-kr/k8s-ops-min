"""Read model joining a live resource to its authoritative GitOps source."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import and_, select

from domains.gitops.models import Application, DeploymentBinding, GitRepository, GitWatchTarget
from domains.inventory.models import ClusterInventoryResourceRecord
from domains.inventory_filter.models import (
    InventoryResourceApplicationVersion,
    InventoryResourceVersion,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gitops import (
    ApplicationStatus,
    DeploymentBindingStatus,
    RepositoryStatus,
    WatchTargetStatus,
)
from packages.storage.engine import DatabaseConnection


class ManifestEditorRepository(DatabaseConnection):
    """Resolve only current, complete application bindings for one inventory key."""

    def list_resource_manifest_sources(
        self,
        *,
        workspace_id: str,
        resource_id: str,
        cluster_id: str,
    ) -> list[JsonObject]:
        resource = InventoryResourceVersion.__table__
        link = InventoryResourceApplicationVersion.__table__
        application = Application.__table__
        binding = DeploymentBinding.__table__
        repository = GitRepository.__table__
        watch = GitWatchTarget.__table__
        statement = (
            select(
                application.c.application_id,
                application.c.name.label("application_name"),
                application.c.manifest_path.label("application_manifest_path"),
                application.c.metadata.label("application_metadata"),
                repository.c.repository_id,
                repository.c.provider,
                repository.c.repo_ref,
                repository.c.default_branch,
                binding.c.binding_id,
                binding.c.environment,
                binding.c.manifest_path,
                binding.c.deploy_policy,
                watch.c.branch.label("watch_branch"),
                watch.c.settings.label("watch_settings"),
            )
            .select_from(
                resource.join(
                    link,
                    and_(
                        link.c.version_id == resource.c.version_id,
                        link.c.workspace_id == resource.c.workspace_id,
                        link.c.cluster_id == resource.c.cluster_id,
                    ),
                )
                .join(
                    application,
                    and_(
                        application.c.workspace_id == link.c.workspace_id,
                        application.c.application_id == link.c.application_id,
                    ),
                )
                .join(
                    repository,
                    and_(
                        repository.c.workspace_id == application.c.workspace_id,
                        repository.c.repository_id == application.c.repository_id,
                    ),
                )
                .join(
                    binding,
                    and_(
                        binding.c.workspace_id == application.c.workspace_id,
                        binding.c.repository_id == application.c.repository_id,
                        binding.c.app_name == application.c.name,
                        binding.c.cluster_id == resource.c.cluster_id,
                        binding.c.namespace == resource.c.namespace,
                    ),
                )
                .outerjoin(
                    watch,
                    and_(
                        watch.c.workspace_id == binding.c.workspace_id,
                        watch.c.repository_id == binding.c.repository_id,
                        watch.c.watch_target_id == binding.c.watch_target_id,
                    ),
                )
            )
            .where(
                resource.c.workspace_id == workspace_id,
                resource.c.inventory_key == resource_id,
                resource.c.cluster_id == cluster_id,
                resource.c.valid_to_revision.is_(None),
                resource.c.application_binding_complete.is_(True),
                application.c.status == ApplicationStatus.ACTIVE.value,
                repository.c.status == RepositoryStatus.ACTIVE.value,
                binding.c.status == DeploymentBindingStatus.ACTIVE.value,
                (watch.c.status.is_(None) | (watch.c.status == WatchTargetStatus.ACTIVE.value)),
            )
            .order_by(application.c.application_id, binding.c.binding_id)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [manifest_source_row(row) for row in rows]

    def resolve_manifest_controller_owner(
        self,
        *,
        workspace_id: str,
        cluster_id: str,
        resource: JsonObject,
    ) -> JsonObject | None:
        """Resolve a Pod to an exact editable controller through retained owner UIDs.

        Names are deliberately never used as a fallback. Every hop must belong to
        the same live inventory snapshot and explicitly declare complete owner
        references, otherwise the editor fails closed.
        """

        if str(resource.get("kind") or "").casefold() != "pod":
            return None
        snapshot_id = str(resource.get("snapshot_id") or "")
        summary = resource.get("summary")
        if not snapshot_id or not isinstance(summary, Mapping):
            return None
        if summary.get("owner_references_complete") is not True:
            return None
        owner_uid = str(summary.get("owner_uid") or "")
        if not owner_uid:
            return None

        allowed = {"deployment", "statefulset", "daemonset"}
        table = ClusterInventoryResourceRecord.__table__
        with self.connection() as conn:
            for _ in range(4):
                row = (
                    conn.execute(
                        select(table)
                        .where(
                            table.c.workspace_id == workspace_id,
                            table.c.cluster_id == cluster_id,
                            table.c.snapshot_id == snapshot_id,
                            table.c.uid == owner_uid,
                            table.c.deleted_at.is_(None),
                        )
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
                current = dict(row)
                if str(current.get("kind") or "").casefold() in allowed:
                    return current
                current_summary = current.get("summary")
                if not isinstance(current_summary, Mapping):
                    return None
                if current_summary.get("owner_references_complete") is not True:
                    return None
                owner_uid = str(current_summary.get("owner_uid") or "")
                if not owner_uid:
                    return None
        return None


def manifest_source_row(row: Any) -> JsonObject:
    application_metadata = dict(row.get("application_metadata") or {})
    deploy_policy = dict(row.get("deploy_policy") or {})
    watch_settings = dict(row.get("watch_settings") or {})
    source_type = str(
        watch_settings.get("source_type")
        or deploy_policy.get("manifest_source")
        or deploy_policy.get("source_type")
        or application_metadata.get("source_type")
        or ""
    )
    return {
        "application_id": str(row["application_id"]),
        "application_name": str(row["application_name"]),
        "repository_id": str(row["repository_id"]),
        "provider": str(row["provider"]),
        "repo_ref": str(row["repo_ref"]),
        "branch": str(row.get("watch_branch") or row["default_branch"]),
        "binding_id": str(row["binding_id"]),
        "environment": str(row["environment"]),
        "manifest_path": str(
            row.get("manifest_path") or row.get("application_manifest_path") or ""
        ),
        "source_type": source_type,
    }
