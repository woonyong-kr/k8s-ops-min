from __future__ import annotations

import domains.rca.repository as rca_repository
from domains.dashboard.repository import (
    OPEN_INCIDENT_STATUSES,
    _rca_timeline_response_columns,
    _resolve_incident_occurrence_id,
    incident_occurrence_key,
    issue_detail_projection,
    timeline_update_from_event,
)
from domains.rca.models import RcaReport
from domains.rca.report_projection import rca_report_projection, rca_report_summary
from domains.rca.repository import rca_report_storage_projection
from packages.contracts.event_bus.interfaces import EventEnvelope
from packages.contracts.event_bus.subjects import EventSubject


def event(subject: EventSubject, payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-1",
        subject=subject.value,
        source="test",
        correlation_id="correlation-1",
        causation_id=None,
        created_at="2026-07-23T21:30:03Z",
        payload=payload,
        workspace_id="default",
    )


def test_rca_report_storage_projection_drops_payload_only_first_seen_at() -> None:
    projection = rca_report_storage_projection(
        {
            "incident": {
                "incident_id": "incident-1",
                "cluster_id": "cluster-1",
                "symptom": "Readiness probe response failure",
                "severity": "medium",
                "first_seen_at": "2026-07-23T20:27:24Z",
                "resource_kind": "ReplicaSet",
                "resource_name": "game-room-0-774544b4fb",
                "namespace": "sandbox",
            },
            "rca_detail": {
                "confidence": 1.0,
                "reason": "candidate authority stayed unready after committed cutover",
            },
        }
    )

    assert "first_seen_at" not in projection
    assert set(projection).issubset(set(RcaReport.__table__.c.keys()))


def test_blocked_rca_report_keeps_analysis_status_in_payload_projection() -> None:
    payload = {
        "analysis_status": "blocked",
        "evidence_ref": "object://evidence/blocked.json",
        "rca_detail": {
            "root_cause": "upstream_unavailable",
            "selected_candidate_id": "upstream_unavailable",
            "confidence": 0.75,
            "supporting_evidence": ["logs:upstream_5xx"],
            "missing_evidence": ["kubernetes:endpoints"],
        },
        "candidates": [{"candidate_id": "upstream_unavailable", "title": "Upstream 장애"}],
        "evaluations": [
            {
                "candidate_id": "upstream_unavailable",
                "score": 0.75,
                "supporting_evidence": ["logs:upstream_5xx"],
                "missing_evidence": ["kubernetes:endpoints"],
            }
        ],
    }

    projected = rca_report_projection(payload)
    summary = rca_report_summary(
        {
            "id": 1,
            "workspace_id": "default",
            "correlation_id": "correlation-1",
            "root_cause": "insufficient_evidence",
            "action": "추가 근거 수집 후 RCA 재분석",
            "payload": payload,
            "created_at": None,
        }
    )

    assert projected["analysis_status"] == "blocked"
    assert projected["candidates"][0]["candidate_id"] == "upstream_unavailable"
    assert summary["analysis_status"] == "blocked"


def test_future_payload_only_projection_field_cannot_become_insert_kwarg(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rca_repository,
        "rca_report_projection",
        lambda _body: {
            "incident_id": "incident-1",
            "confidence": 0.95,
            "future_payload_only_field": "must remain inside payload",
        },
    )

    projection = rca_repository.rca_report_storage_projection({"future": "value"})

    assert projection == {
        "incident_id": "incident-1",
        "confidence": 0.95,
    }


def test_analysis_blocked_projects_its_diagnosis_before_recovery_events() -> None:
    row = timeline_update_from_event(
        event(
            EventSubject.RCA_ANALYSIS_BLOCKED,
            {
                "workspace_id": "default",
                "evidence_ref": "object://evidence/tempo.json",
                "incident": {
                    "incident_id": "incident-tempo",
                    "cluster_id": "cluster-1",
                    "namespace": "target",
                    "resource_kind": "StatefulSet",
                    "resource_name": "tempo",
                    "symptom": "CrashLoopBackOff",
                },
                "rca_detail": {
                    "root_cause": "oom_killed",
                    "confidence": 0.75,
                    "supporting_evidence": [
                        "kubernetes:cluster_resource_state",
                        "logs:related_logs",
                    ],
                    "missing_evidence": ["metrics:telemetry_metrics"],
                },
            },
        )
    )

    assert row is not None
    assert row["status"] == "analysis_blocked"
    assert row["root_cause"] == "oom_killed"
    assert row["confidence"] == 0.75
    assert row["supporting_evidence"] == [
        "kubernetes:cluster_resource_state",
        "logs:related_logs",
    ]
    assert "analysis_blocked" in OPEN_INCIDENT_STATUSES


def test_approval_payload_promotes_nested_diagnosis_and_operator_copy() -> None:
    payload = {
        "workspace_id": "default",
        "recommendation": "user_selection_required",
        "details": {
            "plan": {
                "summary": (
                    "oom_killed 후보가 가장 높은 점수로 평가되었고 추가 근거 수집이 필요합니다."
                ),
                "candidates": [
                    {
                        "title": "대상 워크로드 재시작",
                        "description": "낮은 위험도의 임시 완화 조치입니다.",
                        "draft": {
                            "reason": "Kubernetes 상태와 로그가 OOM 후보를 지지합니다.",
                            "params": {
                                "root_cause": "oom_killed",
                                "confidence": 0.75,
                            },
                        },
                    }
                ],
            },
            "approval_summary": {
                "recommended_candidate": {
                    "title": "대상 워크로드 재시작",
                }
            },
        },
    }

    row = timeline_update_from_event(event(EventSubject.APPROVAL_RECOMMENDED, payload))
    detail = issue_detail_projection(payload)

    assert row is not None
    assert row["root_cause"] == "oom_killed"
    assert row["confidence"] == 0.75
    assert detail == {
        "situation_summary": (
            "oom_killed 후보가 가장 높은 점수로 평가되었고 추가 근거 수집이 필요합니다."
        ),
        "recommended_action_summary": "대상 워크로드 재시작",
        "evidence_summary": "Kubernetes 상태와 로그가 OOM 후보를 지지합니다.",
        "evidence_bundle_summary": (
            "oom_killed 후보가 가장 높은 점수로 평가되었고 추가 근거 수집이 필요합니다."
        ),
    }


def test_completed_payload_projects_narrative_copy_for_issue_detail() -> None:
    detail = issue_detail_projection(
        {
            "incident": {"summary": "room-0 authority handoff is stalled"},
            "root_cause": "handoff_authority_stalled",
            "action": "approval_required",
            "narrative": {
                "executive_summary": "커밋된 Candidate 권위가 Ready로 확정되지 않았습니다.",
                "recommended_action": "동일 Pod를 다음 epoch로 전진 복구합니다.",
            },
            "rca_detail": {
                "evidence_summary": "candidate label과 readiness 503이 함께 관측됐습니다.",
                "evidence_bundle_summary": "5개 provider가 같은 창에서 완료됐습니다.",
            },
        }
    )

    assert detail == {
        "situation_summary": "커밋된 Candidate 권위가 Ready로 확정되지 않았습니다.",
        "recommended_action_summary": "동일 Pod를 다음 epoch로 전진 복구합니다.",
        "evidence_summary": "candidate label과 readiness 503이 함께 관측됐습니다.",
        "evidence_bundle_summary": "5개 provider가 같은 창에서 완료됐습니다.",
    }


def test_resolved_historical_issue_gets_human_situation_summary_at_query_time() -> None:
    payload = {
        "incident": {
            "summary": "ReplicaSet game-room-0의 readiness 응답이 실패했습니다.",
        },
        "evaluations": [
            {
                "reason": "Candidate Pod와 readiness 503 상태를 함께 검토해야 합니다.",
            }
        ],
    }
    columns = {
        column.name
        for column in _rca_timeline_response_columns(include_issue_severity=True)
    }

    assert {
        "situation_summary",
        "recommended_action_summary",
        "evidence_summary",
        "evidence_bundle_summary",
    }.issubset(columns)
    assert issue_detail_projection(payload)["situation_summary"] == (
        "ReplicaSet game-room-0의 readiness 응답이 실패했습니다."
    )


class ScalarResult:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class OccurrenceConnection:
    def __init__(self, values: list[str | None]) -> None:
        self.values = iter(values)
        self.statements: list[object] = []

    def execute(self, statement: object) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(next(self.values, None))


def test_open_correlations_reuse_the_active_incident_occurrence() -> None:
    connection = OccurrenceConnection([None, None, "occurrence-active"])

    occurrence_id = _resolve_incident_occurrence_id(
        connection,
        {
            "workspace_id": "workspace-1",
            "correlation_id": "correlation-new",
            "incident_id": "incident-new",
            "incident_logical_key": "cluster|sandbox|Deployment|api|5xx",
        },
    )

    assert occurrence_id == "occurrence-active"
    assert len(connection.statements) == 3


def test_recurrence_starts_a_new_occurrence_after_no_open_cycle_remains() -> None:
    connection = OccurrenceConnection([None, None, None])

    occurrence_id = _resolve_incident_occurrence_id(
        connection,
        {
            "workspace_id": "workspace-1",
            "correlation_id": "correlation-new",
            "incident_id": "incident-new",
            "incident_logical_key": "cluster|sandbox|Deployment|api|5xx",
        },
    )

    assert occurrence_id == "incident-new"
    assert incident_occurrence_key(
        {
            "incident_occurrence_id": occurrence_id,
            "incident_logical_key": "cluster|sandbox|Deployment|api|5xx",
        }
    ) == "incident-new"


def test_occurrence_projection_is_additive_only_for_the_issue_contract() -> None:
    timeline_columns = {
        column.name for column in _rca_timeline_response_columns()
    }
    issue_columns = {
        column.name
        for column in _rca_timeline_response_columns(include_issue_severity=True)
    }

    assert "incident_occurrence_id" not in timeline_columns
    assert "incident_occurrence_id" in issue_columns
