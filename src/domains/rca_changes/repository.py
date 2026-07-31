"""변경↔장애 상관 projection 저장소."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.audit.models import AuditLog
from domains.command.models import AgentCommand
from domains.dashboard.models import RcaTimeline
from domains.gitops.models import DeploymentBinding, GitRepository, WorkflowRun, WorkflowRunStep
from domains.rca_changes.models import WorkflowPrReference, WorkloadChange
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gitops import (
    DeploymentBindingStatus,
    RepositoryStatus,
    WorkflowRunStatus,
    WorkflowStepName,
    WorkflowStepStatus,
)
from packages.storage.engine import DatabaseConnection


class RcaChangesRepository(DatabaseConnection):
    def get_workflow_pr_identity_context(
        self,
        workspace_id: str,
        workflow_run_id: str,
        application_id: str,
        binding_id: str,
    ) -> JsonObject | None:
        run = WorkflowRun.__table__
        binding = DeploymentBinding.__table__
        repository = GitRepository.__table__
        statement = (
            select(
                run.c.workspace_id,
                run.c.workflow_run_id,
                run.c.application_id,
                run.c.binding_id,
                run.c.commit_sha,
                binding.c.repository_id,
                binding.c.manifest_path,
                repository.c.repo_ref,
            )
            .select_from(
                run.join(
                    binding,
                    and_(
                        binding.c.workspace_id == run.c.workspace_id,
                        binding.c.binding_id == run.c.binding_id,
                        binding.c.cluster_id == run.c.cluster_id,
                        binding.c.environment == run.c.environment,
                    ),
                ).join(
                    repository,
                    and_(
                        repository.c.workspace_id == binding.c.workspace_id,
                        repository.c.repository_id == binding.c.repository_id,
                    ),
                )
            )
            .where(
                run.c.workspace_id == workspace_id,
                run.c.workflow_run_id == workflow_run_id,
                run.c.application_id == application_id,
                run.c.binding_id == binding_id,
                binding.c.status == DeploymentBindingStatus.ACTIVE.value,
                repository.c.status == RepositoryStatus.ACTIVE.value,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row else None

    def get_completed_workload_change_context(
        self,
        workspace_id: str,
        workflow_run_id: str,
        application_id: str,
        binding_id: str,
    ) -> JsonObject | None:
        """성공 배포의 workload key/diff를 권위 read model에서 한 번에 읽는다."""

        run = WorkflowRun.__table__
        binding = DeploymentBinding.__table__
        repository = GitRepository.__table__
        step = WorkflowRunStep.__table__
        diff_step = step.alias("change_diff_step")
        apply_step = step.alias("change_apply_step")
        statement = (
            select(
                run.c.workspace_id,
                run.c.workflow_run_id,
                run.c.application_id,
                run.c.binding_id,
                run.c.cluster_id,
                run.c.commit_sha,
                run.c.command_id,
                run.c.metadata.label("run_metadata"),
                binding.c.repository_id,
                binding.c.namespace,
                binding.c.manifest_path,
                repository.c.repo_ref,
                diff_step.c.details.label("diff_details"),
                apply_step.c.details.label("apply_details"),
            )
            .select_from(
                run.join(
                    binding,
                    and_(
                        binding.c.workspace_id == run.c.workspace_id,
                        binding.c.binding_id == run.c.binding_id,
                        binding.c.cluster_id == run.c.cluster_id,
                    ),
                )
                .join(
                    repository,
                    and_(
                        repository.c.workspace_id == binding.c.workspace_id,
                        repository.c.repository_id == binding.c.repository_id,
                    ),
                )
                .join(
                    diff_step,
                    and_(
                        diff_step.c.workspace_id == run.c.workspace_id,
                        diff_step.c.workflow_run_id == run.c.workflow_run_id,
                        diff_step.c.application_id == run.c.application_id,
                        diff_step.c.binding_id == run.c.binding_id,
                        diff_step.c.environment == run.c.environment,
                        diff_step.c.name == WorkflowStepName.DIFF.value,
                        diff_step.c.status == WorkflowStepStatus.SUCCEEDED.value,
                    ),
                )
                .join(
                    apply_step,
                    and_(
                        apply_step.c.workspace_id == run.c.workspace_id,
                        apply_step.c.workflow_run_id == run.c.workflow_run_id,
                        apply_step.c.application_id == run.c.application_id,
                        apply_step.c.binding_id == run.c.binding_id,
                        apply_step.c.environment == run.c.environment,
                        apply_step.c.name == WorkflowStepName.APPLY.value,
                        apply_step.c.status == WorkflowStepStatus.SUCCEEDED.value,
                    ),
                )
            )
            .where(
                run.c.workspace_id == workspace_id,
                run.c.workflow_run_id == workflow_run_id,
                run.c.application_id == application_id,
                run.c.binding_id == binding_id,
                run.c.status == WorkflowRunStatus.SUCCEEDED.value,
                run.c.command_id.is_not(None),
                binding.c.status == DeploymentBindingStatus.ACTIVE.value,
                binding.c.environment == run.c.environment,
                repository.c.status == RepositoryStatus.ACTIVE.value,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row else None

    def get_completed_workload_change_contexts(
        self,
        workspace_id: str,
        workflow_run_id: str,
        application_id: str,
        binding_id: str,
    ) -> list[JsonObject]:
        """Return one authoritative change context per completed resource command."""

        run = WorkflowRun.__table__
        binding = DeploymentBinding.__table__
        repository = GitRepository.__table__
        command = AgentCommand.__table__
        statement = (
            select(
                run.c.workspace_id,
                run.c.workflow_run_id,
                run.c.application_id,
                run.c.binding_id,
                run.c.cluster_id,
                run.c.commit_sha,
                run.c.metadata.label("run_metadata"),
                binding.c.repository_id,
                binding.c.namespace,
                binding.c.manifest_path,
                repository.c.repo_ref,
                command.c.command_id,
                command.c.result.label("command_result"),
                command.c.payload.label("command_payload"),
            )
            .select_from(
                run.join(
                    binding,
                    and_(
                        binding.c.workspace_id == run.c.workspace_id,
                        binding.c.binding_id == run.c.binding_id,
                        binding.c.cluster_id == run.c.cluster_id,
                        binding.c.environment == run.c.environment,
                    ),
                )
                .join(
                    repository,
                    and_(
                        repository.c.workspace_id == binding.c.workspace_id,
                        repository.c.repository_id == binding.c.repository_id,
                    ),
                )
                .join(
                    command,
                    command.c.payload["workflow_run_id"].astext == run.c.workflow_run_id,
                )
            )
            .where(
                run.c.workspace_id == workspace_id,
                run.c.workflow_run_id == workflow_run_id,
                run.c.application_id == application_id,
                run.c.binding_id == binding_id,
                run.c.status == WorkflowRunStatus.SUCCEEDED.value,
                binding.c.status == DeploymentBindingStatus.ACTIVE.value,
                repository.c.status == RepositoryStatus.ACTIVE.value,
                command.c.workspace_id == workspace_id,
                command.c.status == "completed",
            )
            .order_by(command.c.command_id)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        contexts: list[JsonObject] = []
        for row in rows:
            item = dict(row)
            payload = item.pop("command_payload", None)
            payload_body = dict(payload) if isinstance(payload, dict) else {}
            diff = payload_body.get("diff")
            diff_details = dict(diff) if isinstance(diff, dict) else {}
            diff_details.update(
                {
                    "workspace_id": str(item["workspace_id"]),
                    "repository_id": str(item["repository_id"]),
                    "binding_id": str(item["binding_id"]),
                    "workflow_run_id": str(item["workflow_run_id"]),
                    "cluster_id": str(item["cluster_id"]),
                    "commit_sha": str(item["commit_sha"]),
                    "manifest_path": str(item["manifest_path"]),
                }
            )
            item["diff_details"] = diff_details
            item["command_result"] = dict(item.get("command_result") or {})
            item["run_metadata"] = dict(item.get("run_metadata") or {})
            contexts.append(item)
        return contexts

    def get_completed_workload_resource_diff(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
    ) -> JsonObject | None:
        """Return the one completed, projected diff for an exact workload.

        A workflow step is commit-scoped and cannot identify one resource in a
        multi-resource render.  ``workload_changes`` is written only from a
        successfully completed resource command and carries that command's
        immutable diff payload, so it is the recovery authority read model.
        """

        change = WorkloadChange.__table__
        statement = (
            select(
                change.c.workspace_id,
                change.c.workflow_run_id,
                change.c.binding_id,
                change.c.cluster_id,
                change.c.namespace,
                change.c.resource_kind,
                change.c.resource_name,
                change.c.repository_id,
                change.c.manifest_path,
                change.c.commit_sha,
                change.c.diff_details,
            )
            .where(
                change.c.workspace_id == workspace_id,
                change.c.workflow_run_id == workflow_run_id,
                change.c.binding_id == binding_id,
                change.c.cluster_id == cluster_id,
                change.c.namespace == namespace,
                func.lower(change.c.resource_kind) == resource_kind.casefold(),
                change.c.resource_name == resource_name,
            )
            .limit(2)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        if len(rows) != 1:
            return None
        row = dict(rows[0])
        row["diff_details"] = dict(row.get("diff_details") or {})
        return row

    def list_recent_completed_workload_resource_diffs(
        self,
        workspace_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        *,
        limit: int = 20,
    ) -> list[JsonObject]:
        """Return recent completed diffs for one exact GitOps workload lineage."""

        change = WorkloadChange.__table__
        statement = (
            select(
                change.c.event_id,
                change.c.changed_at,
                change.c.workspace_id,
                change.c.workflow_run_id,
                change.c.binding_id,
                change.c.cluster_id,
                change.c.namespace,
                change.c.resource_kind,
                change.c.resource_name,
                change.c.repository_id,
                change.c.manifest_path,
                change.c.commit_sha,
                change.c.diff_details,
            )
            .where(
                change.c.workspace_id == workspace_id,
                change.c.binding_id == binding_id,
                change.c.cluster_id == cluster_id,
                change.c.namespace == namespace,
                func.lower(change.c.resource_kind) == resource_kind.casefold(),
                change.c.resource_name == resource_name,
            )
            .order_by(change.c.changed_at.desc(), change.c.event_id.desc())
            .limit(max(1, min(limit, 100)))
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        result: list[JsonObject] = []
        for value in rows:
            row = dict(value)
            row["diff_details"] = dict(row.get("diff_details") or {})
            result.append(row)
        return result

    def record_workload_change(self, row: JsonObject) -> None:
        change = WorkloadChange.__table__
        with self.connection() as conn:
            statement = pg_insert(change).values(**row)
            conn.execute(statement.on_conflict_do_nothing())

    def record_workflow_pr_reference(self, row: JsonObject) -> None:
        reference = WorkflowPrReference.__table__
        with self.connection() as conn:
            statement = pg_insert(reference).values(**row)
            saved = conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        reference.c.workspace_id,
                        reference.c.repository_id,
                        reference.c.binding_id,
                        reference.c.workflow_run_id,
                        reference.c.commit_sha,
                        reference.c.manifest_path,
                    ],
                    set_={
                        "source_event_id": statement.excluded.source_event_id,
                        "pr_url": statement.excluded.pr_url,
                        "observed_at": statement.excluded.observed_at,
                        "updated_at": func.now(),
                    },
                    where=or_(
                        statement.excluded.observed_at > reference.c.observed_at,
                        and_(
                            statement.excluded.observed_at == reference.c.observed_at,
                            statement.excluded.source_event_id > reference.c.source_event_id,
                        ),
                    ),
                ).returning(reference.c.workspace_id)
            ).first()
            if saved is None:
                return

    def list_incident_workload_scopes(
        self,
        workspace_id: str,
        incident_id: str,
    ) -> list[JsonObject]:
        statement = _incident_scope_statement(workspace_id, incident_id).limit(2)
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def list_recent_workload_changes(
        self,
        workspace_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        incident_id: str,
        *,
        limit: int = gateway_limits.RCA_RECENT_CHANGE_DEFAULT_LIMIT,
    ) -> list[JsonObject]:
        change = WorkloadChange.__table__
        reference = WorkflowPrReference.__table__
        scope = _incident_scope_statement(workspace_id, incident_id).cte(
            "authorized_incident_scope"
        )
        scope_count = select(func.count()).select_from(scope).scalar_subquery()
        incident_at = (
            select(scope.c.incident_at)
            .where(
                scope.c.cluster_id == cluster_id,
                scope.c.namespace == namespace,
                scope.c.resource_kind == resource_kind.casefold(),
                scope.c.resource_name == resource_name,
            )
            .scalar_subquery()
        )
        statement = (
            select(
                change.c.event_id,
                change.c.changed_at,
                change.c.image_before,
                change.c.image_after,
                reference.c.pr_url,
                change.c.commit_sha,
                change.c.repository_id,
                change.c.repo_ref,
                change.c.workflow_run_id,
                change.c.namespace,
                change.c.resource_kind,
                change.c.resource_name,
            )
            .select_from(
                change.outerjoin(
                    reference,
                    and_(
                        reference.c.workspace_id == change.c.workspace_id,
                        reference.c.repository_id == change.c.repository_id,
                        reference.c.binding_id == change.c.binding_id,
                        reference.c.workflow_run_id == change.c.workflow_run_id,
                        reference.c.commit_sha == change.c.commit_sha,
                        reference.c.manifest_path == change.c.manifest_path,
                    ),
                )
            )
            .where(
                change.c.workspace_id == workspace_id,
                change.c.cluster_id == cluster_id,
                change.c.namespace == namespace,
                change.c.resource_kind == resource_kind.casefold(),
                change.c.resource_name == resource_name,
                scope_count == 1,
                change.c.changed_at <= incident_at,
            )
            .order_by(change.c.changed_at.desc(), change.c.event_id.desc())
            .limit(limit)
        )
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def list_recent_workload_changes_for_incidents(
        self,
        workspace_id: str,
        incident_ids: tuple[str, ...],
        allowed_cluster_ids: set[str],
        *,
        limit: int = 10,
    ) -> list[JsonObject]:
        """Read one globally bounded change sample for an authorized issue page."""
        bounded_incidents = tuple(sorted({value for value in incident_ids if value}))[:100]
        clusters = tuple(sorted({value for value in allowed_cluster_ids if value}))
        if not workspace_id or not bounded_incidents or not clusters:
            return []
        timeline = RcaTimeline.__table__
        audit = AuditLog.__table__
        change = WorkloadChange.__table__
        reference = WorkflowPrReference.__table__
        resource_kind = func.lower(timeline.c.incident_resource_kind).label("resource_kind")
        grouped_scopes = (
            select(
                timeline.c.incident_id,
                timeline.c.cluster_id,
                timeline.c.incident_namespace.label("namespace"),
                resource_kind,
                timeline.c.incident_resource_name.label("resource_name"),
                func.min(audit.c.event_created_at).label("incident_at"),
            )
            .select_from(
                timeline.join(
                    audit,
                    and_(
                        audit.c.workspace_id == timeline.c.workspace_id,
                        audit.c.correlation_id == timeline.c.correlation_id,
                        audit.c.subject == EventSubject.INCIDENT_DETECTED.value,
                        audit.c.event_created_at.is_not(None),
                    ),
                )
            )
            .where(
                timeline.c.workspace_id == workspace_id,
                timeline.c.incident_id.in_(bounded_incidents),
                timeline.c.cluster_id.in_(clusters),
                timeline.c.incident_namespace.is_not(None),
                timeline.c.incident_resource_kind.is_not(None),
                timeline.c.incident_resource_name.is_not(None),
            )
            .group_by(
                timeline.c.incident_id,
                timeline.c.cluster_id,
                timeline.c.incident_namespace,
                resource_kind,
                timeline.c.incident_resource_name,
            )
            .cte("issue_queue_change_scopes")
        )
        ranked_scopes = select(
            grouped_scopes,
            func.count().over(partition_by=grouped_scopes.c.incident_id).label("scope_count"),
        ).cte("ranked_issue_queue_change_scopes")
        valid_scopes = (
            select(ranked_scopes)
            .where(ranked_scopes.c.scope_count == 1)
            .cte("valid_issue_queue_change_scopes")
        )
        statement = (
            select(
                valid_scopes.c.incident_id,
                change.c.event_id,
                change.c.changed_at,
                change.c.image_before,
                change.c.image_after,
                reference.c.pr_url,
                change.c.commit_sha,
                change.c.repository_id,
                change.c.repo_ref,
                change.c.workflow_run_id,
                change.c.namespace,
                change.c.resource_kind,
                change.c.resource_name,
            )
            .select_from(
                valid_scopes.join(
                    change,
                    and_(
                        change.c.workspace_id == workspace_id,
                        change.c.cluster_id == valid_scopes.c.cluster_id,
                        change.c.namespace == valid_scopes.c.namespace,
                        change.c.resource_kind == valid_scopes.c.resource_kind,
                        change.c.resource_name == valid_scopes.c.resource_name,
                        change.c.changed_at <= valid_scopes.c.incident_at,
                    ),
                ).outerjoin(
                    reference,
                    and_(
                        reference.c.workspace_id == change.c.workspace_id,
                        reference.c.repository_id == change.c.repository_id,
                        reference.c.binding_id == change.c.binding_id,
                        reference.c.workflow_run_id == change.c.workflow_run_id,
                        reference.c.commit_sha == change.c.commit_sha,
                        reference.c.manifest_path == change.c.manifest_path,
                    ),
                )
            )
            .order_by(change.c.changed_at.desc(), change.c.event_id.desc())
            .limit(max(1, min(int(limit), 50)))
        )
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def list_recent_workload_changes_for_evidence(
        self,
        workspace_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        changed_before: str,
        *,
        limit: int = gateway_limits.RCA_RECENT_CHANGE_DEFAULT_LIMIT,
    ) -> list[JsonObject]:
        """RCA evidence 생성 시점에 workload/time 기준 최근 GitOps 변경을 읽는다."""

        cutoff = _parse_timestamp(changed_before)
        if cutoff is None:
            return []
        change = WorkloadChange.__table__
        reference = WorkflowPrReference.__table__
        statement = (
            select(
                change.c.event_id,
                change.c.workspace_id,
                change.c.cluster_id,
                change.c.changed_at,
                change.c.image_before,
                change.c.image_after,
                reference.c.pr_url,
                change.c.commit_sha,
                change.c.repository_id,
                change.c.binding_id,
                change.c.repo_ref,
                change.c.workflow_run_id,
                change.c.namespace,
                change.c.resource_kind,
                change.c.resource_name,
                change.c.manifest_path,
                change.c.diff_details,
            )
            .select_from(
                change.outerjoin(
                    reference,
                    and_(
                        reference.c.workspace_id == change.c.workspace_id,
                        reference.c.repository_id == change.c.repository_id,
                        reference.c.binding_id == change.c.binding_id,
                        reference.c.workflow_run_id == change.c.workflow_run_id,
                        reference.c.commit_sha == change.c.commit_sha,
                        reference.c.manifest_path == change.c.manifest_path,
                    ),
                )
            )
            .where(
                change.c.workspace_id == workspace_id,
                change.c.cluster_id == cluster_id,
                change.c.namespace == namespace,
                change.c.resource_kind == resource_kind.casefold(),
                change.c.resource_name == resource_name,
                change.c.changed_at <= cutoff,
            )
            .order_by(change.c.changed_at.desc(), change.c.event_id.desc())
            .limit(limit)
        )
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]


def _incident_scope_statement(workspace_id: str, incident_id: str) -> object:
    timeline = RcaTimeline.__table__
    audit = AuditLog.__table__
    resource_kind = func.lower(timeline.c.incident_resource_kind).label("resource_kind")
    return (
        select(
            timeline.c.cluster_id,
            timeline.c.incident_namespace.label("namespace"),
            resource_kind,
            timeline.c.incident_resource_name.label("resource_name"),
            func.min(audit.c.event_created_at).label("incident_at"),
        )
        .select_from(
            timeline.join(
                audit,
                and_(
                    audit.c.workspace_id == timeline.c.workspace_id,
                    audit.c.correlation_id == timeline.c.correlation_id,
                    audit.c.subject == EventSubject.INCIDENT_DETECTED.value,
                    audit.c.event_created_at.is_not(None),
                ),
            )
        )
        .where(
            timeline.c.workspace_id == workspace_id,
            timeline.c.incident_id == incident_id,
            timeline.c.cluster_id.is_not(None),
            timeline.c.incident_namespace.is_not(None),
            timeline.c.incident_resource_kind.is_not(None),
            timeline.c.incident_resource_name.is_not(None),
        )
        .group_by(
            timeline.c.cluster_id,
            timeline.c.incident_namespace,
            resource_kind,
            timeline.c.incident_resource_name,
        )
    )


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
