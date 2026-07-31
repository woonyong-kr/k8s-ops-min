"""Destructive reset boundary for one marker-owned demo workspace."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, func, select

from domains.demo_workspace.policy import require_demo_workspace_mutation_opt_in
from packages.contracts.demo_workspace import DEMO_SEED_MARKER_KEY
from packages.storage.engine import DatabaseConnection
from packages.storage.schema import metadata


class DemoWorkspaceRepository(DatabaseConnection):
    """Remove a dedicated demo tenant after revalidating its persisted marker."""

    def reconcile_seed_owned_application(
        self,
        *,
        workspace_id: str,
        application_id: str,
        repository_id: str,
        name: str,
        manifest_path: str,
        status: str,
        metadata_: Mapping[str, object],
        expected_marker: Mapping[str, object],
    ) -> dict[str, object] | None:
        """Move one legacy demo application to its current descriptor identity.

        This is deliberately narrower than the product application upsert.  It
        accepts only a row in the same workspace whose persisted seed marker
        belongs to the same descriptor/schema authority.  Public application
        requests never call this boundary.
        """

        require_demo_workspace_mutation_opt_in()
        if not all((workspace_id, application_id, repository_id, name, manifest_path, status)):
            raise ValueError("demo application reconciliation scope must be complete")
        marker = dict(expected_marker)
        if not self._valid_seed_marker(marker):
            raise ValueError("demo application reconciliation marker is invalid")
        if metadata_.get(DEMO_SEED_MARKER_KEY) != marker:
            raise ValueError("demo application reconciliation metadata marker is invalid")

        tables = metadata.tables
        application = tables["applications"]
        repository = tables["git_repositories"]
        with self.unit_of_work() as conn:
            owned_repository_id = conn.execute(
                select(repository.c.repository_id)
                .where(
                    repository.c.workspace_id == workspace_id,
                    repository.c.repository_id == repository_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if owned_repository_id is None:
                return None

            existing = (
                conn.execute(
                    select(application)
                    .where(
                        application.c.workspace_id == workspace_id,
                        application.c.application_id == application_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if existing is None:
                return None
            persisted_metadata = existing.get("metadata")
            persisted_marker = (
                persisted_metadata.get(DEMO_SEED_MARKER_KEY)
                if isinstance(persisted_metadata, Mapping)
                else None
            )
            if not self._same_seed_authority(persisted_marker, marker):
                return None

            target_application_id = conn.execute(
                select(application.c.application_id)
                .where(
                    application.c.workspace_id == workspace_id,
                    application.c.repository_id == repository_id,
                    application.c.name == name,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if target_application_id not in {None, application_id}:
                return None

            updated = (
                conn.execute(
                    application.update()
                    .where(
                        application.c.workspace_id == workspace_id,
                        application.c.application_id == application_id,
                    )
                    .values(
                        repository_id=repository_id,
                        name=name,
                        manifest_path=manifest_path,
                        status=status,
                        metadata=dict(metadata_),
                        updated_at=func.now(),
                    )
                    .returning(application)
                )
                .mappings()
                .first()
            )
        return dict(updated) if updated is not None else None

    def reset_demo_workspace(
        self,
        *,
        workspace_id: str,
        cluster_id: str,
        expected_marker: Mapping[str, object],
        event_source: str,
    ) -> dict[str, int]:
        require_demo_workspace_mutation_opt_in()
        if not workspace_id or not cluster_id:
            raise ValueError("demo reset scope must be non-empty")

        tables = metadata.tables
        registration = tables["cluster_registrations"]
        event = tables["events"]
        event_processing = tables["event_processing"]
        counts: dict[str, int] = {}

        with self.unit_of_work() as conn:
            settings = conn.execute(
                select(registration.c.settings)
                .where(
                    registration.c.workspace_id == workspace_id,
                    registration.c.cluster_id == cluster_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            actual_marker = (
                settings.get(DEMO_SEED_MARKER_KEY) if isinstance(settings, Mapping) else None
            )
            if actual_marker != dict(expected_marker):
                raise RuntimeError("demo reset refused: persisted descriptor marker does not match")

            seed_event_ids = list(
                conn.execute(
                    select(event.c.event_id).where(
                        event.c.source == event_source,
                        event.c.payload["workspace_id"].as_string() == workspace_id,
                    )
                ).scalars()
            )
            if seed_event_ids:
                self._record_delete(
                    counts,
                    "event_processing",
                    conn.execute(
                        delete(event_processing).where(
                            event_processing.c.event_id.in_(seed_event_ids)
                        )
                    ),
                )

            # Reverse metadata order removes FK children before their parents. This is the
            # reset extension point: later demo projections need only retain workspace_id.
            for table in reversed(metadata.sorted_tables):
                if "workspace_id" not in table.c:
                    continue
                self._record_delete(
                    counts,
                    table.name,
                    conn.execute(delete(table).where(table.c.workspace_id == workspace_id)),
                )

            if seed_event_ids:
                self._record_delete(
                    counts,
                    "events",
                    conn.execute(delete(event).where(event.c.event_id.in_(seed_event_ids))),
                )

            self._delete_identity_scope(conn, workspace_id, counts)

        return dict(sorted(counts.items()))

    @classmethod
    def _delete_identity_scope(
        cls,
        conn: Any,
        organization_id: str,
        counts: dict[str, int],
    ) -> None:
        tables = metadata.tables
        assignment = tables["resource_assignments"]
        member_role = tables["member_resource_roles"]
        group = tables["groups"]
        group_member = tables["group_members"]
        organization_member = tables["organization_members"]
        organization = tables["organizations"]

        assignment_ids = select(assignment.c.resource_assignment_id).where(
            assignment.c.organization_id == organization_id
        )
        group_ids = select(group.c.group_id).where(group.c.organization_id == organization_id)

        for name, statement in (
            (
                "member_resource_roles",
                delete(member_role).where(member_role.c.resource_assignment_id.in_(assignment_ids)),
            ),
            (
                "resource_assignments",
                delete(assignment).where(assignment.c.organization_id == organization_id),
            ),
            ("group_members", delete(group_member).where(group_member.c.group_id.in_(group_ids))),
            ("groups", delete(group).where(group.c.organization_id == organization_id)),
            (
                "organization_members",
                delete(organization_member).where(
                    organization_member.c.organization_id == organization_id
                ),
            ),
            (
                "organizations",
                delete(organization).where(organization.c.organization_id == organization_id),
            ),
        ):
            cls._record_delete(counts, name, conn.execute(statement))

    @staticmethod
    def _record_delete(counts: dict[str, int], name: str, result: Any) -> None:
        deleted = max(0, int(result.rowcount or 0))
        if deleted:
            counts[name] = counts.get(name, 0) + deleted

    @staticmethod
    def _valid_seed_marker(marker: object) -> bool:
        return bool(
            isinstance(marker, Mapping)
            and isinstance(marker.get("descriptor_id"), str)
            and isinstance(marker.get("schema_version"), int)
            and isinstance(marker.get("digest"), str)
            and re.fullmatch(r"[0-9a-f]{64}", str(marker["digest"]))
        )

    @classmethod
    def _same_seed_authority(
        cls,
        persisted_marker: object,
        expected_marker: Mapping[str, object],
    ) -> bool:
        if not isinstance(persisted_marker, Mapping):
            return False
        return bool(
            cls._valid_seed_marker(persisted_marker)
            and persisted_marker["descriptor_id"] == expected_marker["descriptor_id"]
            and persisted_marker["schema_version"] == expected_marker["schema_version"]
        )
