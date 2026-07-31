from __future__ import annotations

from dataclasses import replace

from conftest import load_service, run_handler

from domains.command.events import (
    CommandCompletedBody,
    CommandQueuedForAgentBody,
    CommandRequestedBody,
)
from domains.gitops.events import (
    ApprovalGrantedBody,
    ApprovalRequestedBody,
    DesiredDesiredDiffDetectedBody,
    Diff,
    DiffAnalyzedBody,
)
from domains.scm.events import SafePrRequestedBody
from domains.timeline.repository import TimelineLedgerAppend
from packages.config.constants import RiskLevel
from packages.contracts.gitops import WorkflowMutation
from packages.contracts.timeline import TimelineEvent


class StandaloneCommandDb:
    async def get_workflow_run(self, workflow_run_id: str) -> None:
        assert workflow_run_id == "workflow-manifest-edit-standalone"
        return None

    async def upsert_application(self, payload: object) -> None:
        raise AssertionError("standalone commands must not synthesize GitOps applications")


def test_standalone_manifest_command_does_not_create_gitops_run() -> None:
    service = load_service("gitops/workflow-controller")
    event = CommandQueuedForAgentBody(
        command_id="command-a",
        cluster_id="cluster-a",
        workspace_id="workspace-a",
        application_id="application-a",
        workflow_run_id="workflow-manifest-edit-standalone",
        binding_id="binding-a",
        environment="production",
        direct_execution=True,
        direct_execution_confirmed=True,
    )

    assert run_handler(service.on_command_queued, event, StandaloneCommandDb()) == []


class MultiApprovalWorkflowDb:
    def __init__(
        self,
        *,
        transition_applied: bool,
        progress: dict[str, object] | None = None,
        snapshot_handled: int = 2,
    ):
        self.transition_applied = transition_applied
        self.progress = progress or {}
        self.snapshot_handled = snapshot_handled
        self.attached: list[str] = []
        self.snapshots_recorded = 0

    async def resolve_workflow_approval(self, payload: object) -> object:
        return payload

    async def update_workflow_run(self, payload: dict[str, object]) -> WorkflowMutation:
        applied = self.transition_applied
        self.transition_applied = False
        return WorkflowMutation(applied=applied)

    async def record_workflow_step(self, payload: object) -> WorkflowMutation:
        return WorkflowMutation(applied=True)

    async def append_timeline_event(self, event: TimelineEvent) -> TimelineLedgerAppend:
        return TimelineLedgerAppend(event=event, sequence=1, inserted=True)

    async def attach_workflow_command(self, workflow_run_id: str, command_id: str) -> None:
        self.attached.append(command_id)

    async def get_workflow_identity_for_command(self, command_id: str) -> dict[str, str]:
        return {
            "workflow_run_id": "workflow-a",
            "workspace_id": "workspace-a",
            "application_id": "application-a",
            "binding_id": "binding-a",
            "environment": "production",
            "cluster_id": "cluster-a",
            "commit_sha": "commit-a",
        }

    async def get_workflow_command_progress(self, workflow_run_id: str) -> dict[str, object]:
        assert workflow_run_id == "workflow-a"
        return self.progress

    async def record_approved_workflow_snapshots(self, workflow_run_id: str) -> int:
        assert workflow_run_id == "workflow-a"
        self.snapshots_recorded += 1
        return self.snapshot_handled


class DiffApprovalDb(MultiApprovalWorkflowDb):
    def __init__(self) -> None:
        super().__init__(transition_applied=True)

    async def update_workflow_run(self, payload: dict[str, object]) -> WorkflowMutation:
        return WorkflowMutation(applied=True)


class MergedRecoveryApprovalDb(DiffApprovalDb):
    def __init__(self, *, tampered_commit: bool = False) -> None:
        super().__init__()
        self.tampered_commit = tampered_commit
        self.resolved: list[tuple[object, ...]] = []
        self.requested: list[dict[str, object]] = []

    async def request_workflow_approval(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self.requested.append(payload)
        return payload

    async def resolve_workflow_approval_if_open(
        self,
        approval_id: str,
        workspace_id: str,
        status: str,
        decided_by: str,
        decision: str,
        details: dict[str, object],
    ) -> bool:
        self.resolved.append(
            (
                approval_id,
                workspace_id,
                status,
                decided_by,
                decision,
                details,
            )
        )
        return True

    async def get_recovery_plan_for_workflow(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        application_id: str,
    ) -> dict[str, object]:
        assert (
            workspace_id,
            workflow_run_id,
            binding_id,
            application_id,
        ) == ("workspace-a", "workflow-a", "binding-a", "application-a")
        commit_sha = "tampered-merge" if self.tampered_commit else "merge-a"
        return {
            "plan_id": "plan-a",
            "workspace_id": "workspace-a",
            "correlation_id": "correlation-a",
            "status": "deploy_pending",
            "selected_action_id": "action-a",
            "payload": {
                "execution_route": "safe_pr",
                "target": {
                    "cluster_id": "cluster-a",
                    "namespace": "sandbox",
                    "resource_kind": "Deployment",
                    "resource_name": "api-server",
                },
                "lifecycle": {
                    "phase": "deploy_pending",
                    "pr": {
                        "url": "https://github.com/acme/game/pull/18",
                        "head_sha": "head-a",
                        "repository_id": "repository-a",
                        "repo_ref": "acme/game",
                        "base_branch": "main",
                        "binding_id": "binding-a",
                        "application_id": "application-a",
                        "environment": "production",
                        "cluster_id": "cluster-a",
                        "manifest_path": "deploy/k8s",
                    },
                    "merge": {
                        "pr_url": "https://github.com/acme/game/pull/18",
                        "head_sha": "head-a",
                        "merge_commit_sha": commit_sha,
                        "workflow_run_id": "workflow-a",
                        "repository_id": "repository-a",
                        "binding_id": "binding-a",
                        "application_id": "application-a",
                        "cluster_id": "cluster-a",
                        "deployment_request": {
                            "commit_sha": "merge-a",
                            "image": "demo/game:v2",
                            "replicas": 2,
                            "correlation_id": "correlation-a",
                            "workspace_id": "workspace-a",
                            "repository_id": "repository-a",
                            "repo_ref": "acme/game",
                            "branch": "main",
                            "watch_target_id": "watch-a",
                            "binding_id": "binding-a",
                            "application_id": "application-a",
                            "workflow_run_id": "workflow-a",
                            "environment": "production",
                            "cluster_id": "cluster-a",
                            "manifest_path": "deploy/k8s",
                            "source_type": "kustomize",
                            "force": True,
                        },
                    },
                    "verification": {
                        "target": {
                            "cluster_id": "cluster-a",
                            "namespace": "sandbox",
                            "resource_kind": "Deployment",
                            "resource_name": "api-server",
                        },
                        "expected": {"replicas": 2},
                    },
                    "authorization": {
                        "target": {
                            "cluster_id": "cluster-a",
                            "namespace": "sandbox",
                            "resource_kind": "Deployment",
                            "resource_name": "api-server",
                        },
                        "changes": [
                            {
                                "field_path": "spec.replicas",
                                "current_value": 1,
                                "desired_value": 2,
                            }
                        ],
                    },
                },
            },
        }

    async def get_workflow_run(self, workflow_run_id: str) -> dict[str, object]:
        assert workflow_run_id == "workflow-a"
        return {
            "workflow_run_id": "workflow-a",
            "workspace_id": "workspace-a",
            "application_id": "application-a",
            "binding_id": "binding-a",
            "environment": "production",
            "cluster_id": "cluster-a",
            "commit_sha": "merge-a",
            "status": "waiting_for_approval",
        }


def analyzed_diff(resource: str, artifact: str) -> DiffAnalyzedBody:
    return DiffAnalyzedBody(
        diff=Diff(
            resource=resource,
            namespace="sandbox",
            desired_image="",
            actual_image="",
            risk=RiskLevel.REVIEW_REQUIRED,
            workspace_id="workspace-a",
            repository_id="repository-a",
            application_id="application-a",
            workflow_run_id="workflow-a",
            binding_id="binding-a",
            environment="production",
            cluster_id="cluster-a",
            manifest_path="deploy/k8s",
            desired_manifest={
                "apiVersion": "apps/v1",
                "kind": resource.split("/", 1)[0],
                "metadata": {
                    "name": resource.split("/", 1)[1],
                    "namespace": "sandbox",
                },
                "spec": {"replicas": 2},
            },
            status="intended_change",
            has_changes=True,
            changes=[
                {
                    "field_path": "spec.replicas",
                    "classification": "intended_change",
                    "old_desired": 1,
                    "live": 1,
                    "new_desired": 2,
                    "before": 1,
                    "after": 2,
                }
            ],
            basis={"artifact_digest": artifact},
        ),
        safe=False,
        risk="review-required",
        reason="operator approval required",
    )


def test_diff_analysis_creates_resource_scoped_approvals_and_commands() -> None:
    service = load_service("gitops/workflow-controller")
    analyzer = load_service("gitops/diff-analyze-worker")
    db = DiffApprovalDb()
    first_diff = analyzed_diff(
        "Deployment/api-server",
        "sha256:" + "a" * 64,
    )
    second_diff = analyzed_diff(
        "Service/api-server",
        "sha256:" + "b" * 64,
    )

    first = run_handler(
        service.on_diff_analyzed,
        first_diff,
        db,
    )
    second = run_handler(
        service.on_diff_analyzed,
        second_diff,
        db,
    )
    approvals = [
        body
        for body in (*first, *second)
        if isinstance(body, ApprovalRequestedBody)
    ]

    assert len(approvals) == 2
    assert approvals[0].approval_id != approvals[1].approval_id
    first_policy = analyzer.evaluate_safe_pr_policy(first_diff.diff)
    second_policy = analyzer.evaluate_safe_pr_policy(second_diff.diff)
    assert approvals[0].approval_id == first_policy.approval_ref
    assert approvals[1].approval_id == second_policy.approval_ref
    safe_pr = analyzer.build_safe_pr_request_body(first_diff.diff, first_policy)
    assert safe_pr.cluster_id == "cluster-a"
    assert safe_pr.target_namespace == "sandbox"
    assert safe_pr.target_resource == "Deployment/api-server"
    assert safe_pr.target_authority == "policy_approval"
    assert approvals[0].details["diff"]["resource"] == "Deployment/api-server"
    assert approvals[1].details["diff"]["resource"] == "Service/api-server"

    commands = []
    for approval in approvals:
        emitted = run_handler(
            service.on_approval_granted,
            approval_granted(
                approval.approval_id,
                str(approval.details["diff"]["resource"]),
            ),
            db,
        )
        commands.extend(
            body for body in emitted if isinstance(body, CommandRequestedBody)
        )

    assert [command.approval_ref for command in commands] == [
        approvals[0].approval_id,
        approvals[1].approval_id,
    ]


def test_exact_merged_recovery_pr_reuses_human_approval_and_dispatches_command() -> None:
    service = load_service("gitops/workflow-controller")
    db = MergedRecoveryApprovalDb()

    emitted = run_handler(
        service.on_diff_analyzed,
        analyzed_diff("Deployment/api-server", "sha256:" + "a" * 64),
        db,
    )

    assert not any(isinstance(body, ApprovalRequestedBody) for body in emitted)
    commands = [body for body in emitted if isinstance(body, CommandRequestedBody)]
    assert len(commands) == 1
    assert commands[0].workflow_run_id == "workflow-a"
    assert commands[0].binding_id == "binding-a"
    assert commands[0].cluster_id == "cluster-a"
    assert commands[0].requested_by == "recovery-pr-merge"
    assert db.resolved[0][2:5] == (
        "granted",
        "recovery-pr-merge",
        "approved-by-merged-recovery-pr",
    )


def test_converged_merged_recovery_dispatches_one_idempotent_command() -> None:
    service = load_service("gitops/workflow-controller")
    db = MergedRecoveryApprovalDb()
    analyzed = analyzed_diff("Deployment/api-server", "sha256:" + "a" * 64)
    converged = replace(
        analyzed,
        diff=replace(
            analyzed.diff,
            status="already_converged",
            has_changes=False,
            changes=[
                {
                    "field_path": "spec.replicas",
                    "classification": "already_converged",
                    "old_desired": 1,
                    "live": 2,
                    "new_desired": 2,
                    "before": 2,
                    "after": 2,
                }
            ],
        ),
    )

    emitted = run_handler(service.on_diff_analyzed, converged, db)

    assert not any(isinstance(body, ApprovalRequestedBody) for body in emitted)
    commands = [body for body in emitted if isinstance(body, CommandRequestedBody)]
    assert len(commands) == 1
    assert commands[0].requested_by == "recovery-pr-merge"


def test_no_change_merged_recovery_creates_no_second_pr_and_dispatches_once() -> None:
    analyzer = load_service("gitops/diff-analyze-worker")
    controller = load_service("gitops/workflow-controller")
    db = MergedRecoveryApprovalDb()
    analyzed = analyzed_diff("Deployment/api-server", "sha256:" + "a" * 64)
    no_change = replace(
        analyzed.diff,
        status="no_change",
        has_changes=False,
        changes=[],
    )

    policy_events = run_handler(
        analyzer.on_desired_diff,
        DesiredDesiredDiffDetectedBody(diff=no_change),
        db,
    )
    projected = run_handler(controller.on_diff_analyzed, policy_events[0], db)

    assert not any(isinstance(body, SafePrRequestedBody) for body in policy_events)
    assert not any(isinstance(body, ApprovalRequestedBody) for body in projected)
    commands = [body for body in projected if isinstance(body, CommandRequestedBody)]
    assert len(commands) == 1
    assert commands[0].requested_by == "recovery-pr-merge"


def test_exact_merged_recovery_does_not_create_a_second_safe_pr() -> None:
    analyzer = load_service("gitops/diff-analyze-worker")
    db = MergedRecoveryApprovalDb()
    diff = replace(
        analyzed_diff("Deployment/api-server", "sha256:" + "a" * 64).diff,
        risk=RiskLevel.SANDBOX_ONLY,
    )

    emitted = run_handler(
        analyzer.on_desired_diff,
        DesiredDesiredDiffDetectedBody(diff=diff),
        db,
    )

    assert not any(isinstance(body, SafePrRequestedBody) for body in emitted)
    assert db.requested[0]["status"] == "requested"
    details = db.requested[0]["details"]
    assert isinstance(details, dict)
    assert details["policy_route"] == "recovery_pr_merged"


def test_out_of_scope_resource_in_recovery_run_creates_no_pr_or_approval() -> None:
    analyzer = load_service("gitops/diff-analyze-worker")
    controller = load_service("gitops/workflow-controller")
    db = MergedRecoveryApprovalDb()
    diff = analyzed_diff("RoleBinding/api-server", "sha256:" + "b" * 64)

    analyzed = run_handler(
        analyzer.on_desired_diff,
        DesiredDesiredDiffDetectedBody(diff=diff.diff),
        db,
    )
    projected = run_handler(
        controller.on_diff_analyzed,
        analyzed[0],
        db,
    )

    assert not any(isinstance(body, SafePrRequestedBody) for body in analyzed)
    assert not any(isinstance(body, ApprovalRequestedBody) for body in projected)
    assert not any(isinstance(body, CommandRequestedBody) for body in projected)
    assert db.requested == []


def test_tampered_merged_recovery_identity_fails_closed_without_second_approval() -> None:
    service = load_service("gitops/workflow-controller")
    db = MergedRecoveryApprovalDb(tampered_commit=True)

    emitted = run_handler(
        service.on_diff_analyzed,
        analyzed_diff("Deployment/api-server", "sha256:" + "a" * 64),
        db,
    )

    assert not any(isinstance(body, ApprovalRequestedBody) for body in emitted)
    assert not any(isinstance(body, CommandRequestedBody) for body in emitted)


def approval_granted(approval_id: str, resource: str) -> ApprovalGrantedBody:
    command = CommandRequestedBody.from_body(
        {
            "cluster_id": "cluster-a",
            "action": "apply_manifest",
            "namespace": "sandbox",
            "reason": "approved",
            "diff": {
                "resource": resource,
                "namespace": "sandbox",
                "desired_image": "",
                "actual_image": "",
                "risk": "review-required",
                "workspace_id": "workspace-a",
                "application_id": "application-a",
                "workflow_run_id": "workflow-a",
                "binding_id": "binding-a",
                "environment": "production",
                "cluster_id": "cluster-a",
            },
            "workspace_id": "workspace-a",
            "application_id": "application-a",
            "workflow_run_id": "workflow-a",
            "binding_id": "binding-a",
            "environment": "production",
            "approval_ref": approval_id,
            "policy_decision_ref": f"policy:{approval_id}",
        }
    )
    return ApprovalGrantedBody(
        approval_id=approval_id,
        workflow_run_id="workflow-a",
        application_id="application-a",
        workspace_id="workspace-a",
        binding_id="binding-a",
        environment="production",
        decided_by="operator-a",
        details={"command_requested": command.to_body()},
    )


def completed_result(resource: str) -> dict[str, object]:
    return {
        "status": "completed",
        "applied": True,
        "resources": [{"resource": resource, "applied": True}],
        "rollout": {"ready": True},
    }


def test_every_granted_approval_emits_its_command_after_run_is_already_applying() -> None:
    service = load_service("gitops/workflow-controller")
    first_db = MultiApprovalWorkflowDb(transition_applied=True)
    later_db = MultiApprovalWorkflowDb(transition_applied=False)

    first = run_handler(
        service.on_approval_granted,
        approval_granted("approval-a", "deployment/api"),
        first_db,
    )
    later = run_handler(
        service.on_approval_granted,
        approval_granted("approval-b", "service/api"),
        later_db,
    )

    assert [body.approval_ref for body in first if isinstance(body, CommandRequestedBody)] == [
        "approval-a"
    ]
    assert [body.approval_ref for body in later if isinstance(body, CommandRequestedBody)] == [
        "approval-b"
    ]


def test_first_successful_command_does_not_complete_multi_approval_workflow() -> None:
    service = load_service("gitops/workflow-controller")
    db = MultiApprovalWorkflowDb(
        transition_applied=True,
        progress={
            "approvals": [
                {"approval_id": "approval-a", "status": "granted"},
                {"approval_id": "approval-b", "status": "granted"},
            ],
            "commands": [
                {
                    "command_id": "command-a",
                    "approval_ref": "approval-a",
                    "status": "completed",
                    "result": completed_result("deployment/api"),
                }
            ],
        },
    )

    emitted = run_handler(
        service.on_command_completed,
        CommandCompletedBody(
            command_id="command-a",
            result=completed_result("deployment/api"),
        ),
        db,
    )

    assert emitted == []
    assert db.snapshots_recorded == 0


def test_workflow_completes_once_all_approval_commands_succeed() -> None:
    service = load_service("gitops/workflow-controller")
    db = MultiApprovalWorkflowDb(
        transition_applied=True,
        progress={
            "approvals": [
                {"approval_id": "approval-a", "status": "granted"},
                {"approval_id": "approval-b", "status": "granted"},
            ],
            "commands": [
                {
                    "command_id": "command-a",
                    "approval_ref": "approval-a",
                    "status": "completed",
                    "result": completed_result("deployment/api"),
                },
                {
                    "command_id": "command-b",
                    "approval_ref": "approval-b",
                    "status": "completed",
                    "result": completed_result("service/api"),
                },
            ],
        },
    )

    emitted = run_handler(
        service.on_command_completed,
        CommandCompletedBody(
            command_id="command-b",
            result=completed_result("service/api"),
        ),
        db,
    )

    completed = [body for body in emitted if body.__subject__ == "workflow.run.completed"]
    assert len(completed) == 1
    assert completed[0].details["command_ids"] == ["command-a", "command-b"]
    assert db.snapshots_recorded == 1


def test_workflow_completes_when_mixed_kind_commands_are_all_handled() -> None:
    service = load_service("gitops/workflow-controller")
    commands = [
        {
            "command_id": "command-deployment",
            "approval_ref": "approval-deployment",
            "status": "completed",
            "result": completed_result("deployment/api-server"),
        },
        {
            "command_id": "command-service",
            "approval_ref": "approval-service",
            "status": "completed",
            "result": completed_result("service/login-gateway-api"),
        },
        {
            "command_id": "command-pdb",
            "approval_ref": "approval-pdb",
            "status": "completed",
            "result": completed_result("poddisruptionbudget/api-server"),
        },
    ]
    db = MultiApprovalWorkflowDb(
        transition_applied=True,
        snapshot_handled=len(commands),
        progress={
            "approvals": [
                {
                    "approval_id": command["approval_ref"],
                    "status": "granted",
                }
                for command in commands
            ],
            "commands": commands,
        },
    )

    emitted = run_handler(
        service.on_command_completed,
        CommandCompletedBody(
            command_id="command-pdb",
            result=completed_result("poddisruptionbudget/api-server"),
        ),
        db,
    )

    completed = [body for body in emitted if body.__subject__ == "workflow.run.completed"]
    assert len(completed) == 1
    assert completed[0].details["command_ids"] == [
        "command-deployment",
        "command-pdb",
        "command-service",
    ]
    assert db.snapshots_recorded == 1
