from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from domains.rca import router as rca_router
from domains.rca.events import (
    HealingActionDraft,
    RcaActionRequiredBody,
    RecoveryActionCandidate,
    RecoveryActionSelectedBody,
    RecoveryPlan,
)
from services.ai.agent.recovery.dispatch import RecoveryActionPreflight
from services.ai.agent.recovery.engine import with_execution_eligibility


def recovery_candidate(
    *,
    route: str = "draft_pr",
    namespace: str = "sandbox",
    action_type: str = "probe_fix",
    params: dict[str, Any] | None = None,
) -> RecoveryActionCandidate:
    return RecoveryActionCandidate(
        action_id="action-1",
        title="Readiness probe 수정",
        description="GitOps manifest의 readiness probe를 수정합니다.",
        draft=HealingActionDraft(
            action_type=action_type,
            namespace=namespace,
            resource_kind="Deployment",
            resource_name="game-room",
            reason="probe path mismatch",
            risk_level="medium",
            dry_run=True,
            source_evidence=["object://evidence/correlation-1.json"],
            params=params or {},
        ),
        route=route,
        rank=1,
        score=0.95,
        risk_level="medium",
        blast_radius="single workload",
        approval_required=True,
        prerequisites=[],
        validation_checks=["readiness probe succeeds"],
        rollback_plan="revert the PR",
        evidence_refs=["object://evidence/correlation-1.json"],
    )


def recovery_plan(candidate: RecoveryActionCandidate) -> RecoveryPlan:
    return RecoveryPlan(
        plan_id="plan-1",
        incident_id="incident-1",
        evidence_ref="object://evidence/correlation-1.json",
        summary="probe path mismatch",
        target={
            "workspace_id": "workspace-1",
            "cluster_id": "cluster-1",
            "namespace": candidate.draft.namespace,
            "resource_kind": "Deployment",
            "resource_name": "game-room",
        },
        recommended_action_id=candidate.action_id,
        execution_route=candidate.route,
        selection_required=True,
        candidates=[candidate],
    )


def recovery_record(plan: RecoveryPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "correlation_id": "correlation-1",
        "payload": plan.to_body(),
    }


class SelectionDb:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.selection_calls: list[tuple[str, str, str, str]] = []
        self.approval_payloads: list[dict[str, Any]] = []

    def select_recovery_plan_action_if_open(
        self,
        plan_id: str,
        workspace_id: str,
        action_id: str,
        selected_by: str,
    ) -> dict[str, Any]:
        self.trace.append("select")
        self.selection_calls.append((plan_id, workspace_id, action_id, selected_by))
        return {"correlation_id": "correlation-1", "payload": {}}

    def request_workflow_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.trace.append("approval")
        self.approval_payloads.append(payload)
        return {"workflow_run_id": payload["workflow_run_id"]}


class RecordingEvents:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.bodies: list[object] = []

    async def accept_body(
        self,
        body: object,
        *,
        correlation_id: str,
        actor: object,
    ) -> object:
        subject = str(getattr(body, "__subject__", "unknown"))
        self.trace.append(f"event:{subject}")
        self.bodies.append(body)
        return SimpleNamespace(
            event=SimpleNamespace(
                event_id=f"event-{len(self.bodies)}",
                correlation_id=correlation_id,
            )
        )


class BlockingPreflight:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    async def prepare(
        self,
        evt: RecoveryActionSelectedBody,
        correlation_id: str,
    ) -> RcaActionRequiredBody:
        self.trace.append("preflight")
        return RcaActionRequiredBody(
            reason="승인 snapshot·binding·repository 권위 context를 확보하지 못했습니다.",
            evidence_ref=evt.plan.evidence_ref,
            workspace_id=evt.workspace_id,
            reason_code="gitops_authority_unavailable",
            missing_evidence=["gitops_authority_context"],
            diagnostics={
                "plan_id": evt.plan.plan_id,
                "action_id": evt.selected.action_id,
            },
        )


class SuccessfulPreflight:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    async def prepare(
        self,
        evt: RecoveryActionSelectedBody,
        correlation_id: str,
    ) -> RecoveryActionCandidate:
        self.trace.append("preflight")
        assert correlation_id == "correlation-1"
        return replace(
            evt.selected,
            draft=replace(
                evt.selected.draft,
                params={
                    **evt.selected.draft.params,
                    "workspace_id": "workspace-1",
                    "repository_id": "repository-1",
                    "binding_id": "binding-1",
                    "application_id": "application-1",
                    "workflow_run_id": "workflow-1",
                    "environment": "sandbox",
                    "manifest_path": "deploy/game-room.yaml",
                    "repo_ref": "Jungle-303-04/final",
                    "base_branch": "dev",
                    "commit_sha": "a" * 40,
                },
            ),
        )


def current_user() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="operator-1",
        workspace_id="workspace-1",
        roles=["release_operator"],
    )


def run_selection(
    *,
    db: SelectionDb,
    events: RecordingEvents,
    preflight: object | None,
    candidate: RecoveryActionCandidate | None = None,
) -> object:
    candidate = candidate or recovery_candidate()
    plan = recovery_plan(candidate)
    return asyncio.run(
        rca_router._select_recovery_action_from_record(
            recovery_record(plan),
            candidate.action_id,
            "operator approved",
            expected_plan_id=plan.plan_id,
            current=current_user(),
            db=db,
            events=events,
            preflight=preflight,
        )
    )


def test_safe_pr_preflight_failure_keeps_plan_open_and_records_only_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    db = SelectionDb(trace)
    events = RecordingEvents(trace)
    monkeypatch.setattr(rca_router, "require_cluster_access", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as raised:
        run_selection(db=db, events=events, preflight=BlockingPreflight(trace))

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "gitops_authority_unavailable",
        "detail": "승인 snapshot·binding·repository 권위 context를 확보하지 못했습니다.",
        "missing_evidence": ["gitops_authority_context"],
        "next_actions": [],
        "retryable": True,
    }
    assert trace == ["preflight", "event:rca.action_required"]
    assert db.selection_calls == []
    assert db.approval_payloads == []
    assert len(events.bodies) == 1
    assert isinstance(events.bodies[0], RcaActionRequiredBody)


def test_safe_pr_preflight_success_persists_authority_before_selected_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    db = SelectionDb(trace)
    events = RecordingEvents(trace)
    monkeypatch.setattr(rca_router, "require_cluster_access", lambda *args, **kwargs: None)

    response = run_selection(db=db, events=events, preflight=SuccessfulPreflight(trace))

    assert response.accepted is True
    assert response.correlation_id == "correlation-1"
    assert trace == [
        "preflight",
        "select",
        "approval",
        "event:recovery.action_selected",
    ]
    assert db.selection_calls == [("plan-1", "workspace-1", "action-1", "operator-1")]
    assert len(db.approval_payloads) == 1
    approval = db.approval_payloads[0]
    assert approval["workflow_run_id"] == "workflow-1"
    assert approval["binding_id"] == "binding-1"
    assert approval["application_id"] == "application-1"
    selected = events.bodies[0]
    assert isinstance(selected, RecoveryActionSelectedBody)
    assert selected.selected.draft.params["repository_id"] == "repository-1"
    assert selected.selected.draft.params["workflow_run_id"] == "workflow-1"
    assert selected.selected.draft.params["approval_ref"].startswith("approval-")
    assert selected.selected.draft.params["policy_decision_ref"].startswith("recovery:")


def test_missing_preflight_fails_closed_without_selecting_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    db = SelectionDb(trace)
    events = RecordingEvents(trace)
    monkeypatch.setattr(rca_router, "require_cluster_access", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as raised:
        run_selection(db=db, events=events, preflight=None)

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "safe_pr_preflight_unavailable"
    assert trace == ["event:rca.action_required"]
    assert db.selection_calls == []
    assert db.approval_payloads == []


def test_auto_preflight_rejects_disallowed_namespace_before_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    db = SelectionDb(trace)
    events = RecordingEvents(trace)
    candidate = recovery_candidate(
        route="auto",
        namespace="target",
        action_type="rollout_restart",
        params={"command": "rollout_restart"},
    )
    monkeypatch.setenv("CONTROL_ALLOWED_NAMESPACES", "sandbox,color-turf")
    monkeypatch.setattr(rca_router, "require_cluster_access", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as raised:
        run_selection(
            db=db,
            events=events,
            preflight=RecoveryActionPreflight(authority=None),
            candidate=candidate,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "control_namespace_not_allowed"
    assert "target 네임스페이스" in raised.value.detail["detail"]
    assert raised.value.detail["retryable"] is True
    assert trace == ["event:rca.action_required"]
    assert db.selection_calls == []
    assert db.approval_payloads == []
    blocker = events.bodies[0]
    assert isinstance(blocker, RcaActionRequiredBody)
    assert blocker.diagnostics["namespace"] == "target"
    assert blocker.diagnostics["control_allowed_namespaces"] == [
        "sandbox",
        "color-turf",
    ]


def test_planner_marks_disallowed_auto_candidate_before_operator_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_ALLOWED_NAMESPACES", "sandbox,color-turf")
    candidate = recovery_candidate(
        route="auto",
        namespace="target",
        action_type="rollout_restart",
        params={"command": "rollout_restart"},
    )

    evaluated = with_execution_eligibility(candidate)

    assert evaluated.executable is False
    assert evaluated.blocked_reason_code == "control_namespace_not_allowed"
    assert "target 네임스페이스" in str(evaluated.blocked_reason)


def test_missing_auto_preflight_fails_closed_without_selecting_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    db = SelectionDb(trace)
    events = RecordingEvents(trace)
    candidate = recovery_candidate(
        route="auto",
        namespace="sandbox",
        action_type="rollout_restart",
        params={"command": "rollout_restart"},
    )
    monkeypatch.setattr(rca_router, "require_cluster_access", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as raised:
        run_selection(
            db=db,
            events=events,
            preflight=None,
            candidate=candidate,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "recovery_action_preflight_unavailable"
    assert trace == ["event:rca.action_required"]
    assert db.selection_calls == []
    assert db.approval_payloads == []
