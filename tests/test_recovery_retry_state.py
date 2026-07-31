from __future__ import annotations

import asyncio
from typing import Any

from conftest import load_service, run_handler

from domains.command.events import CommandRejectedBody
from domains.rca.events import RcaActionRequiredBody, RcaFollowupRequiredBody
from domains.scm.events import SafePrFailedBody

feedback_worker = load_service("ai/rca-feedback-worker")


class RetryDb:
    def __init__(
        self,
        *,
        reopened: bool = True,
        approval: dict[str, Any] | None = None,
        recovery_plan: dict[str, Any] | None = None,
    ) -> None:
        self.reopened = reopened
        self.approval = approval
        self.recovery_plan = recovery_plan
        self.reopen_calls: list[tuple[str, str, str]] = []
        self.approval_calls: list[tuple[str, str]] = []
        self.correlation_calls: list[tuple[str, str]] = []
        self.transitions: list[tuple[str, str, tuple[str, ...], str, dict[str, Any]]] = []

    async def reopen_recovery_plan_action(
        self,
        plan_id: str,
        workspace_id: str,
        action_id: str,
    ) -> bool:
        self.reopen_calls.append((plan_id, workspace_id, action_id))
        return self.reopened

    async def get_workflow_approval(
        self,
        approval_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        self.approval_calls.append((approval_id, workspace_id))
        return self.approval

    async def get_recovery_plan_by_correlation(
        self,
        correlation_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        self.correlation_calls.append((correlation_id, workspace_id))
        return self.recovery_plan

    async def update_recovery_plan_lifecycle_if_status(
        self,
        plan_id: str,
        workspace_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        lifecycle: dict[str, Any],
    ) -> dict[str, Any] | None:
        self.transitions.append(
            (plan_id, workspace_id, expected_statuses, status, lifecycle)
        )
        return {"plan_id": plan_id} if self.reopened else None


def action_required(reason_code: str) -> RcaActionRequiredBody:
    return RcaActionRequiredBody(
        reason="recovery dispatch blocked",
        evidence_ref="object://evidence/correlation-1.json",
        workspace_id="workspace-1",
        reason_code=reason_code,
        diagnostics={
            "plan_id": "plan-1",
            "action_id": "action-1",
        },
    )


def safe_pr_failed(*, approval_ref: str | None = "approval-1") -> SafePrFailedBody:
    details: dict[str, object] = {}
    if approval_ref is not None:
        details["approval_ref"] = approval_ref
    return SafePrFailedBody(
        provider="github",
        title="recovery PR",
        reason="GitHub rejected the request",
        workspace_id="workspace-1",
        reason_code="github_api_error",
        details=details,
    )


def command_rejected() -> CommandRejectedBody:
    return CommandRejectedBody(
        reason="namespace is not allowed",
        reason_code="namespace_not_allowed",
        requested={"workspace_id": "workspace-1"},
    )


def test_command_rejection_reopens_selected_recovery_action() -> None:
    db = RetryDb(
        recovery_plan={
            "plan_id": "plan-1",
            "evidence_ref": "evidence-1",
            "status": "selected",
            "selected_action_id": "action-1",
        }
    )

    emitted = run_handler(
        feedback_worker.on_recovery_command_rejected,
        command_rejected(),
        db=db,
        correlation_id="correlation-1",
    )

    assert db.reopen_calls == [("plan-1", "workspace-1", "action-1")]
    assert len(emitted) == 1
    assert isinstance(emitted[0], RcaFollowupRequiredBody)
    assert emitted[0].reason_code == "namespace_not_allowed"
    assert emitted[0].next_actions[0]["action_type"] == "review_recovery_action"


def test_unrelated_command_rejection_does_not_change_recovery_selection() -> None:
    db = RetryDb(recovery_plan=None)

    emitted = run_handler(
        feedback_worker.on_recovery_command_rejected,
        command_rejected(),
        db=db,
        correlation_id="correlation-1",
    )

    assert emitted == []
    assert db.reopen_calls == []


def test_retryable_action_required_reopens_only_the_selected_action() -> None:
    db = RetryDb()

    emitted = run_handler(
        feedback_worker.on_rca_action_required,
        action_required("gitops_authority_unavailable"),
        db=db,
        correlation_id="correlation-1",
    )

    assert db.reopen_calls == [("plan-1", "workspace-1", "action-1")]
    assert len(emitted) == 1
    assert isinstance(emitted[0], RcaFollowupRequiredBody)
    assert emitted[0].reason_code == "gitops_authority_unavailable"


def test_manual_action_required_does_not_reopen_recovery_selection() -> None:
    db = RetryDb()

    emitted = run_handler(
        feedback_worker.on_rca_action_required,
        action_required("manual_review_required"),
        db=db,
        correlation_id="correlation-1",
    )

    assert db.reopen_calls == []
    assert len(emitted) == 1
    assert isinstance(emitted[0], RcaFollowupRequiredBody)


def test_preflight_blocker_keeps_followup_when_plan_is_already_open() -> None:
    db = RetryDb(
        reopened=False,
        recovery_plan={
            "plan_id": "plan-1",
            "status": "selection_requested",
            "selected_action_id": None,
        },
    )

    emitted = run_handler(
        feedback_worker.on_rca_action_required,
        action_required("gitops_authority_unavailable"),
        db=db,
        correlation_id="correlation-1",
    )

    assert len(emitted) == 1
    assert isinstance(emitted[0], RcaFollowupRequiredBody)


def test_stale_action_required_cannot_block_a_different_selected_action() -> None:
    db = RetryDb(
        reopened=False,
        recovery_plan={
            "plan_id": "plan-1",
            "status": "selected",
            "selected_action_id": "new-action",
        },
    )

    emitted = run_handler(
        feedback_worker.on_rca_action_required,
        action_required("gitops_authority_unavailable"),
        db=db,
        correlation_id="correlation-1",
    )

    assert emitted == []


def test_safe_pr_failure_uses_approval_identity_to_preserve_retryable_action() -> None:
    db = RetryDb(
        approval={
            "details": {
                "recovery_plan_id": "plan-from-approval",
                "recovery_action_id": "action-from-approval",
            }
        },
        recovery_plan={
            "plan_id": "plan-from-approval",
            "status": "selected",
            "selected_action_id": "action-from-approval",
            "payload": {"lifecycle": {"phase": "selected"}},
        },
    )

    emitted = run_handler(
        feedback_worker.on_recovery_safe_pr_failed,
        safe_pr_failed(),
        db=db,
        correlation_id="correlation-1",
    )

    assert db.approval_calls == [("approval-1", "workspace-1")]
    assert db.correlation_calls == [("correlation-1", "workspace-1")]
    assert db.reopen_calls == []
    assert db.transitions[0][0:4] == (
        "plan-from-approval",
        "workspace-1",
        ("selected",),
        "failed",
    )
    assert db.transitions[0][4]["failure"]["stage"] == "safe_pr"
    assert len(emitted) == 1
    assert isinstance(emitted[0], RcaFollowupRequiredBody)
    assert emitted[0].diagnostics["plan_id"] == "plan-from-approval"
    assert emitted[0].diagnostics["action_id"] == "action-from-approval"


def test_legacy_safe_pr_failure_falls_back_to_selected_same_correlation_plan() -> None:
    db = RetryDb(
        recovery_plan={
            "plan_id": "legacy-plan",
            "status": "selected",
            "selected_action_id": "legacy-action",
        }
    )

    identity = asyncio.run(
        feedback_worker.failed_safe_pr_recovery_identity(
            safe_pr_failed(approval_ref=None),
            feedback_worker.EventContext(
                event_id="event-1",
                subject="safe_pr.failed",
                correlation_id="correlation-1",
                causation_id=None,
                db=db,
            ),
        )
    )

    assert identity == ("legacy-plan", "legacy-action")
    assert db.correlation_calls == [("correlation-1", "workspace-1")]


def test_general_safe_pr_failure_without_recovery_identity_is_ignored() -> None:
    db = RetryDb()

    emitted = run_handler(
        feedback_worker.on_recovery_safe_pr_failed,
        safe_pr_failed(approval_ref=None),
        db=db,
        correlation_id="correlation-1",
    )

    assert emitted == []
    assert db.reopen_calls == []


def test_stale_safe_pr_failure_does_not_emit_retry_followup_when_cas_fails() -> None:
    db = RetryDb(
        reopened=False,
        approval={
            "details": {
                "recovery_plan_id": "plan-1",
                "recovery_action_id": "stale-action",
            }
        },
        recovery_plan={
            "plan_id": "plan-1",
            "status": "selected",
            "selected_action_id": "stale-action",
            "payload": {"lifecycle": {"phase": "selected"}},
        },
    )

    emitted = run_handler(
        feedback_worker.on_recovery_safe_pr_failed,
        safe_pr_failed(),
        db=db,
        correlation_id="correlation-1",
    )

    assert db.reopen_calls == []
    assert db.transitions[0][0:4] == (
        "plan-1",
        "workspace-1",
        ("selected",),
        "failed",
    )
    assert emitted == []
