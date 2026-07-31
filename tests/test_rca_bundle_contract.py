from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from domains.rca_bundle.serializer import remediation_bundle_response


def _report() -> dict[str, object]:
    return {
        "id": 1,
        "workspace_id": "default",
        "correlation_id": "correlation-1",
        "root_cause": "oom_killed",
        "action": "restart",
        "incident_id": "incident-1",
        "cluster_id": "target-1",
        "created_at": "2026-07-24T00:00:00Z",
    }


def _candidate() -> dict[str, object]:
    return {
        "action_id": "action-1",
        "title": "메모리 제한 조정",
        "description": "관측된 메모리 사용량에 맞춰 제한을 조정합니다.",
        "draft": {
            "action_type": "patch_workload",
            "namespace": "target",
            "resource_kind": "StatefulSet",
            "resource_name": "tempo",
            "reason": "OOMKilled 재발 방지",
            "risk_level": "medium",
            "dry_run": True,
            "source_evidence": ["metrics:telemetry_metrics"],
            "params": {"memory_limit": "1Gi"},
        },
        "route": "draft_pr",
        "rank": 1,
        "score": 0.9,
        "risk_level": "medium",
        "blast_radius": "target_workload",
        "approval_required": True,
        "prerequisites": ["현재 workload manifest 확인"],
        "validation_checks": ["OOMKilled 재발 없음"],
        "rollback_plan": "이전 메모리 제한으로 복원합니다.",
        "evidence_refs": ["metrics:telemetry_metrics"],
        "recommendation_reason": "원인에 직접 대응하는 최소 변경입니다.",
        "expected_outcome": "Pod가 안정적으로 Ready 상태를 유지합니다.",
        "risk_explanation": "재시작이 한 차례 발생할 수 있습니다.",
        "rollback_reason": "메모리 사용량이 개선되지 않으면 복원합니다.",
    }


def _recovery(candidate: dict[str, object]) -> dict[str, object]:
    return {
        "status": "selection_requested",
        "selected_action_id": None,
        "selected_by": None,
        "evidence_ref": "object://evidence/correlation-1.json",
        "payload": {"candidates": [candidate]},
    }


def test_bundle_preserves_canonical_recovery_candidate_explanations() -> None:
    candidate = _candidate()

    response = remediation_bundle_response(_report(), _recovery(candidate))

    assert response.remediation is not None
    serialized = response.remediation.candidates[0]
    assert serialized.recommendation_reason == candidate["recommendation_reason"]
    assert serialized.expected_outcome == candidate["expected_outcome"]
    assert serialized.risk_explanation == candidate["risk_explanation"]
    assert serialized.rollback_reason == candidate["rollback_reason"]


def test_bundle_accepts_legacy_candidate_without_explanations() -> None:
    candidate = _candidate()
    for field in (
        "recommendation_reason",
        "expected_outcome",
        "risk_explanation",
        "rollback_reason",
    ):
        candidate.pop(field)

    response = remediation_bundle_response(_report(), _recovery(candidate))

    assert response.remediation is not None
    serialized = response.remediation.candidates[0]
    assert serialized.recommendation_reason is None
    assert serialized.expected_outcome is None
    assert serialized.risk_explanation is None
    assert serialized.rollback_reason is None


def test_bundle_rejects_unknown_candidate_fields() -> None:
    candidate = deepcopy(_candidate())
    candidate["internal_signal"] = "must-not-leak"

    with pytest.raises(ValidationError, match="internal_signal"):
        remediation_bundle_response(_report(), _recovery(candidate))
