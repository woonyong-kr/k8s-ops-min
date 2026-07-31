from __future__ import annotations

from domains.rca.events import EvidenceBundle, EvidenceItem, IncidentRecord
from services.ai.agent.causes.engine import analyze_root_cause, evaluate_causes, plan_causes
from services.ai.agent.causes.signals import extract_bundle_signals
from services.ai.agent.defaults import ActionRoutes
from services.ai.agent.playbooks.recovery import registered_recovery_rules

HANDOFF_ROLE_FACT = "pod_label:opsia.dev/game-role=candidate"
READINESS_503 = "Readiness probe failed: HTTP probe failed with statuscode: 503"


def incident() -> IncidentRecord:
    return IncidentRecord(
        incident_id="incident-room-0",
        cluster_id="game-server111-7224",
        resource_kind="ReplicaSet",
        resource_name="game-room-0-774544b4fb",
        namespace="sandbox",
        symptom="Readiness probe response failure",
        severity="warning",
        first_seen_at="2026-07-23T20:27:24Z",
        summary="game-room-0 readiness probe returns 503",
    )


def evidence_bundle(*, candidate_role: bool = True) -> EvidenceBundle:
    labels = {
        "app": "game-server",
        "opsia.dev/room-id": "room-0",
    }
    if candidate_role:
        labels["opsia.dev/game-role"] = "candidate"
    return EvidenceBundle(
        incident_id="incident-room-0",
        items=[
            EvidenceItem(
                source="kubernetes",
                name="cluster_resource_state",
                value={
                    "pods": [
                        {
                            "name": "game-room-0-774544b4fb-pwrwh",
                            "namespace": "sandbox",
                            "labels": labels,
                        }
                    ],
                    "events": [
                        {
                            "reason": "Unhealthy",
                            "message": READINESS_503,
                        }
                    ],
                },
                summary="candidate room Pod remains unready with HTTP 503",
            ),
            EvidenceItem(
                source="metadata",
                name="current_workload_snapshots",
                value={
                    "workloads": [
                        {
                            "workload": {
                                "kind": "Deployment",
                                "namespace": "sandbox",
                                "name": "game-room-0",
                            }
                        }
                    ]
                },
                summary="current workload snapshot",
            ),
        ],
        missing_evidence=[],
        complete=True,
    )


def evaluation_by_id(bundle: EvidenceBundle) -> dict[str, object]:
    plan = plan_causes(incident(), bundle, "object://evidence/incident-room-0.json")
    return {
        evaluation.candidate_id: evaluation
        for evaluation in evaluate_causes(plan.candidates, bundle, plan.rule_missing)
    }


def test_candidate_pod_label_is_extracted_as_a_handoff_authority_fact() -> None:
    signals = extract_bundle_signals(evidence_bundle())

    assert HANDOFF_ROLE_FACT in signals.facts


def test_handoff_candidate_and_readiness_503_select_authority_stall_without_app_trace() -> None:
    bundle = evidence_bundle()
    plan = plan_causes(incident(), bundle, "object://evidence/incident-room-0.json")
    evaluations = evaluate_causes(plan.candidates, bundle, plan.rule_missing)

    handoff = next(
        evaluation
        for evaluation in evaluations
        if evaluation.candidate_id == "handoff_authority_stalled"
    )
    detail = analyze_root_cause(evaluations)

    assert handoff.score == 1.0
    assert handoff.missing_evidence == []
    assert "traces:related_traces" not in handoff.missing_evidence
    assert detail.root_cause == "handoff_authority_stalled"


def test_plain_readiness_503_does_not_claim_probe_path_mismatch() -> None:
    evaluations = evaluation_by_id(evidence_bundle(candidate_role=False))
    probe_path = evaluations["probe_path_wrong"]

    assert "signal:probe_path_failure_signal" in probe_path.missing_evidence


def test_handoff_authority_stall_recommends_approved_forward_recovery_not_probe_edit() -> None:
    matching = [
        rule
        for rule in registered_recovery_rules()
        if "handoff_authority_stalled" in getattr(rule, "root_causes", ())
    ]

    assert len(matching) == 1
    action = matching[0].action_specs[0]
    assert action.action_type == "handoff_authority_recovery"
    assert action.route == ActionRoutes().approval_required
    assert action.risk_level == "high"
    assert action.params["strategy"] == "forward_reconcile_committed_candidate"
