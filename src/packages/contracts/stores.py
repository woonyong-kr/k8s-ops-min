"""워커가 ctx.db 로 보는 능력별 store(async). 서비스는 자기 store 만 봄.

ctx.db 는 AsyncDb 로 감싸져 모든 메서드가 async → 여기 메서드도 async.
핸들러가 `ctx: EventContext[RcaStore]` 로 받으면 IDE·타입체커가 그 store 의
메서드만 노출한다(다른 서비스 DB 능력은 안 보임).
"""

from __future__ import annotations

from typing import Protocol

from packages.contracts.event_bus.interfaces import EventEnvelope, JsonObject
from packages.contracts.gitops import WorkflowMutation
from packages.contracts.timeline import TimelineEvent


class RcaStore(Protocol):
    async def save_evidence(
        self, correlation_id: str, workspace_id: str, kind: str, body: JsonObject
    ) -> None: ...

    async def upsert_rca_enriched_evidence_window(
        self,
        *,
        evidence_key: str,
        workspace_id: str,
        cluster_id: str,
        correlation_id: str,
        window_start: str,
        source_id: str,
        agent_id: str | None,
        payload: JsonObject,
    ) -> bool: ...

    async def get_evidence_payload(
        self, workspace_id: str, correlation_id: str, kind: str
    ) -> JsonObject | None: ...

    async def claim_incident_signal(
        self,
        workspace_id: str,
        cluster_id: str,
        signal_key: str,
        correlation_id: str,
        payload: JsonObject,
    ) -> bool: ...

    async def append_timeline_event(self, event: TimelineEvent) -> object: ...

    async def get_evidence_window(self, evidence_key: str) -> JsonObject | None: ...

    async def get_evidence_window_payload(self, evidence_key: str) -> JsonObject | None: ...

    async def list_aligned_evidence_window_payloads(
        self,
        workspace_id: str,
        cluster_id: str,
        observed_at: str,
        *,
        exclude_source_id: str,
        before_seconds: int = 600,
        after_seconds: int = 60,
        limit: int = 12,
    ) -> list[JsonObject]: ...

    async def list_aligned_alertmanager_window_payloads(
        self,
        workspace_id: str,
        cluster_id: str,
        observed_at: str,
        *,
        source_id: str,
        before_seconds: int = 60,
        after_seconds: int = 600,
        limit: int = 12,
    ) -> list[JsonObject]: ...

    async def save_rca_report(
        self,
        correlation_id: str,
        workspace_id: str,
        root_cause: str,
        action: str,
        body: JsonObject,
    ) -> None: ...

    async def find_recent_rca_report(
        self,
        workspace_id: str,
        root_cause: str,
        resource_key: str,
        window_seconds: int,
    ) -> JsonObject | None: ...

    async def list_recent_workload_changes_for_evidence(
        self,
        workspace_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        changed_before: str,
        *,
        limit: int = 5,
    ) -> list[JsonObject]: ...


class RcaBacklogStore(Protocol):
    async def upsert_rca_backlog_item(self, body: JsonObject) -> None: ...

    async def resolve_rca_backlog_item_for_rule(
        self, workspace_id: str, symptom: str, reason: str
    ) -> int: ...


class RecoveryPlanStore(Protocol):
    async def get_cluster_registration(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> JsonObject | None: ...

    async def get_evidence_payload(
        self,
        workspace_id: str,
        correlation_id: str,
        kind: str,
    ) -> JsonObject | None: ...

    async def upsert_recovery_plan(
        self,
        correlation_id: str,
        workspace_id: str,
        plan: JsonObject,
        *,
        status: str,
        selected_action_id: str | None = None,
        selected_by: str | None = None,
    ) -> None: ...

    async def upsert_recovery_selection_request(
        self,
        correlation_id: str,
        workspace_id: str,
        plan: JsonObject,
    ) -> None: ...

    async def reopen_recovery_plan_action(
        self,
        plan_id: str,
        workspace_id: str,
        action_id: str,
    ) -> bool: ...

    async def get_recovery_plan_by_correlation(
        self,
        correlation_id: str,
        workspace_id: str,
    ) -> JsonObject | None: ...

    async def get_workflow_approval(
        self,
        approval_id: str,
        workspace_id: str = "default",
    ) -> JsonObject | None: ...

    async def update_recovery_plan_lifecycle_if_status(
        self,
        plan_id: str,
        workspace_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        lifecycle: JsonObject,
        clear_selection: bool = False,
    ) -> JsonObject | None: ...

    async def get_recovery_plan_for_workflow(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        application_id: str,
    ) -> JsonObject | None: ...

    async def list_recovery_verification_plans(
        self,
        workspace_id: str,
        cluster_id: str,
        *,
        limit: int = 100,
    ) -> list[JsonObject]: ...

    async def expire_recovery_verifications(
        self,
        *,
        now: object | None = None,
        limit: int = 100,
    ) -> list[JsonObject]: ...

    async def get_evidence_window_payload_for_workspace(
        self,
        workspace_id: str,
        evidence_key: str,
    ) -> JsonObject | None: ...

    async def list_alert_events(
        self,
        workspace_id: str,
        *,
        from_time: object | None = None,
        rule_name: str | None = None,
        source: str | None = None,
        incident_ids: tuple[str, ...] | None = None,
        event_ids: tuple[str, ...] | None = None,
        subject_key: str | None = None,
        limit: int = 100,
    ) -> list[JsonObject]: ...

    async def current_database_time(self) -> object: ...

    async def get_workflow_run(self, workflow_run_id: str) -> JsonObject | None: ...


class RepoChangeStore(Protocol):
    async def save_repo_change(
        self,
        correlation_id: str,
        commit_sha: str,
        manifest: JsonObject,
        workspace_id: str = "default",
        repository_id: str | None = None,
        watch_target_id: str | None = None,
        binding_id: str | None = None,
        manifest_path: str | None = None,
    ) -> None: ...

    async def record_manifest_artifact(self, payload: JsonObject) -> JsonObject: ...

    async def find_rendered_manifest_artifacts(
        self,
        workspace_id: str,
        binding_id: str,
        commit_sha: str,
        manifest_path: str,
        renderer_version: str,
    ) -> list[JsonObject]: ...

    async def get_last_approved_resource_snapshot(
        self,
        workspace_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource: str,
    ) -> JsonObject | None: ...


class ReleaseFlowStore(Protocol):
    async def project_release_workflow_event(self, payload: JsonObject) -> JsonObject | None: ...

    async def queue_evidence_jobs(self, **payload: object) -> JsonObject: ...


class RcaChangesStore(Protocol):
    async def get_completed_workload_resource_diff(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
    ) -> JsonObject | None: ...

    async def list_recent_completed_workload_resource_diffs(
        self,
        workspace_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        *,
        limit: int = 20,
    ) -> list[JsonObject]: ...

    async def get_completed_workload_change_contexts(
        self,
        workspace_id: str,
        workflow_run_id: str,
        application_id: str,
        binding_id: str,
    ) -> list[JsonObject]: ...

    async def get_completed_workload_change_context(
        self,
        workspace_id: str,
        workflow_run_id: str,
        application_id: str,
        binding_id: str,
    ) -> JsonObject | None: ...

    async def get_workflow_pr_identity_context(
        self,
        workspace_id: str,
        workflow_run_id: str,
        application_id: str,
        binding_id: str,
    ) -> JsonObject | None: ...

    async def record_workload_change(self, row: JsonObject) -> None: ...

    async def record_workflow_pr_reference(self, row: JsonObject) -> None: ...


class WorkflowStore(Protocol):
    async def get_cluster_registration(
        self, workspace_id: str, cluster_id: str
    ) -> JsonObject | None: ...

    async def get_application(
        self, workspace_id: str, application_id: str
    ) -> JsonObject | None: ...

    async def get_deployment_binding(
        self, workspace_id: str, binding_id: str
    ) -> JsonObject | None: ...

    async def get_workflow_run(self, workflow_run_id: str) -> JsonObject | None: ...

    async def get_recovery_plan_for_workflow(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        application_id: str,
    ) -> JsonObject | None: ...

    async def upsert_application(self, payload: JsonObject) -> JsonObject: ...

    async def start_workflow_run(self, payload: JsonObject) -> WorkflowMutation: ...

    async def update_workflow_run(self, payload: JsonObject) -> WorkflowMutation: ...

    async def record_workflow_step(self, payload: JsonObject) -> WorkflowMutation: ...

    async def request_workflow_approval(self, payload: JsonObject) -> JsonObject: ...

    async def resolve_workflow_approval(self, payload: JsonObject) -> JsonObject: ...

    async def resolve_workflow_approval_if_open(
        self,
        approval_id: str,
        workspace_id: str,
        status: str,
        decided_by: str | None,
        decision: str | None,
        details: JsonObject,
    ) -> bool: ...

    async def attach_workflow_command(self, workflow_run_id: str, command_id: str) -> None: ...

    async def get_workflow_command_progress(self, workflow_run_id: str) -> JsonObject: ...

    async def record_approved_workflow_snapshots(self, workflow_run_id: str) -> int: ...

    async def update_workflow_run_for_command(self, payload: JsonObject) -> WorkflowMutation: ...

    async def append_timeline_event(self, event: TimelineEvent) -> object: ...

    async def get_workflow_identity_for_command(self, command_id: str) -> JsonObject | None: ...


class PolicyDecisionStore(Protocol):
    async def request_workflow_approval(self, payload: JsonObject) -> JsonObject: ...

    async def resolve_workflow_approval(self, payload: JsonObject) -> JsonObject: ...


class PullRequestStore(Protocol):
    async def get_workflow_approval(
        self,
        approval_id: str,
        workspace_id: str = "default",
    ) -> JsonObject | None: ...

    async def get_completed_workload_resource_diff(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
    ) -> JsonObject | None: ...

    async def save_pull_request(
        self, correlation_id: str, pr_url: str, title: str, body: str, status: str
    ) -> None: ...


class AgentCommandStore(Protocol):
    async def get_workflow_approval(
        self, approval_id: str, workspace_id: str = "default"
    ) -> JsonObject | None: ...

    async def queue_agent_command(
        self, correlation_id: str, plan: JsonObject, status: str
    ) -> bool: ...

    async def fail_expired_agent_commands(
        self, *, queue_ttl_seconds: int = 1800
    ) -> list[JsonObject]: ...


class TargetReconcileStore(Protocol):
    async def list_target_desired_states(
        self, workspace_id: str, cluster_id: str
    ) -> list[JsonObject]: ...

    async def record_target_reconcile_result(self, payload: JsonObject) -> JsonObject: ...


class AuditStore(Protocol):
    async def append_audit_log(self, evt: EventEnvelope) -> None: ...

    async def append_audit_logs(self, rows: list[JsonObject]) -> None: ...


class DashboardStore(Protocol):
    async def get_evidence_payload(
        self, workspace_id: str, correlation_id: str, kind: str
    ) -> JsonObject | None: ...

    async def upsert_rca_timeline(self, row: JsonObject) -> None: ...


class AiConversationStore(Protocol):
    async def record_ai_response(self, payload: JsonObject) -> bool: ...

    async def record_ai_failure(self, payload: JsonObject) -> bool: ...
