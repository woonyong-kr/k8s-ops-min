from dataclasses import replace

import pytest

from domains.rca.events import HealingActionDraft, RecoveryActionCandidate, RecoveryPlan
from packages.config.constants import Command
from services.ai.agent.recovery.catalog import registered_recovery_rules
from services.ai.agent.recovery.dispatch import build_command_request_body


def test_builtin_recovery_never_prescribes_a_fixed_replica_count() -> None:
    replica_actions = [
        action
        for rule in registered_recovery_rules()
        for action in rule.action_specs
        if action.action_type in {"deployment_scale", "replica_scale"}
    ]

    assert replica_actions
    assert all("replicas" not in action.params for action in replica_actions)
    assert all("3 replicas" not in action.description for action in replica_actions)
    assert all(
        not any("replica 3" in check for check in action.validation_checks)
        for action in replica_actions
    )


def scale_candidate(*, replicas: object = 2) -> RecoveryActionCandidate:
    return RecoveryActionCandidate(
        action_id="scale-1",
        title="승인된 용량 복원",
        description="정확한 Deployment replica 수를 복원합니다.",
        draft=HealingActionDraft(
            action_type="deployment_scale",
            namespace="sandbox",
            resource_kind="Deployment",
            resource_name="lobby",
            reason="capacity saturated",
            risk_level="medium",
            dry_run=False,
            source_evidence=["object://evidence/1"],
            params={
                "workspace_id": "workspace-1",
                "cluster_id": "cluster-1",
                "environment": "sandbox",
                "replicas": replicas,
            },
        ),
        route="auto",
        rank=1,
        score=1.0,
        risk_level="medium",
        blast_radius="single workload",
        approval_required=True,
        prerequisites=[],
        validation_checks=[],
        rollback_plan="restore previous replicas",
        evidence_refs=["object://evidence/1"],
    )


def scale_plan(candidate: RecoveryActionCandidate) -> RecoveryPlan:
    return RecoveryPlan(
        plan_id="plan-1",
        incident_id="incident-1",
        evidence_ref="object://evidence/1",
        summary="restore lobby capacity",
        target={
            "workspace_id": "workspace-1",
            "cluster_id": "cluster-1",
            "environment": "sandbox",
            "namespace": "sandbox",
            "resource_kind": "Deployment",
            "resource_name": "lobby",
        },
        recommended_action_id=candidate.action_id,
        execution_route=candidate.route,
        selection_required=True,
        candidates=[candidate],
    )


def test_auto_scale_requires_exact_target_and_positive_replica_count() -> None:
    candidate = scale_candidate(replicas=2)
    request = build_command_request_body(
        scale_plan(candidate),
        candidate,
        selected_by="operator-1",
        auto_selected=False,
    )

    assert request is not None
    assert request.cluster_id == "cluster-1"
    assert request.namespace == "sandbox"
    assert request.environment == "sandbox"
    assert request.action == Command.KUBERNETES_DEPLOYMENT_SCALE_ACTION
    assert request.payload == {
        "namespace": "sandbox",
        "name": "lobby",
        "replicas": 2,
    }


@pytest.mark.parametrize("replicas", [None, True, False, 0, -1, "2", 2.5])
def test_auto_scale_never_guesses_or_coerces_replica_count(replicas: object) -> None:
    candidate = scale_candidate(replicas=replicas)

    assert build_command_request_body(
        scale_plan(candidate),
        candidate,
        selected_by="operator-1",
        auto_selected=False,
    ) is None


@pytest.mark.parametrize(
    "missing",
    ["workspace_id", "cluster_id", "environment", "namespace"],
)
def test_auto_recovery_fails_closed_without_authoritative_target(missing: str) -> None:
    candidate = scale_candidate()
    plan = scale_plan(candidate)
    target = {key: value for key, value in plan.target.items() if key != missing}
    params = {
        key: value
        for key, value in candidate.draft.params.items()
        if key != missing
    }
    candidate = replace(
        candidate,
        draft=replace(candidate.draft, params=params),
    )
    if missing == "namespace":
        candidate = replace(
            candidate,
            draft=replace(candidate.draft, namespace=""),
        )
    plan = replace(plan, target=target, candidates=[candidate])

    assert build_command_request_body(
        plan,
        candidate,
        selected_by="operator-1",
        auto_selected=False,
    ) is None


def test_auto_recovery_rejects_conflicting_target_identity() -> None:
    candidate = scale_candidate()
    plan = scale_plan(candidate)
    plan = replace(plan, target={**plan.target, "cluster_id": "other-cluster"})

    assert build_command_request_body(
        plan,
        candidate,
        selected_by="operator-1",
        auto_selected=False,
    ) is None
