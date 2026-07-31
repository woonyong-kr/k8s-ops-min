import pytest
from sqlalchemy import create_engine, literal, select
from sqlalchemy.dialects import postgresql

from domains.dashboard.models import RcaTimeline
from domains.dashboard.repository import (
    MAX_RCA_REPORT_SUMMARY_BATCH,
    RECOMMENDED_ACTION_SUMMARY_PATHS,
    _rca_timeline_response_columns,
    effective_confidence_column,
    issue_detail_projection,
    latest_rca_issue_report_summaries_statement,
    projected_incident_status,
)
from domains.dashboard.router import issue_item
from domains.issue_filter.repository import _authorized_issues


def test_issue_scan_does_not_probe_reports_for_every_candidate_row() -> None:
    statement = select(*_rca_timeline_response_columns(include_issue_severity=True))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "rca_reports" not in sql
    assert "rca_issue_report_summary" not in sql
    assert "rca_timeline.payload" in sql
    assert "reason_code" in sql


@pytest.mark.parametrize(
    ("existing", "incoming", "expected"),
    [
        ("approval_recommended", "followup_required", "approval_recommended"),
        ("pr_open", "rca_completed", "pr_open"),
        ("rca_evaluated", "followup_required", "followup_required"),
    ],
)
def test_weak_reanalysis_cannot_replace_an_unresolved_recovery_pin(
    existing: str,
    incoming: str,
    expected: str,
) -> None:
    statement = select(
        projected_incident_status(
            literal(existing),
            literal(incoming),
            literal(True),
        )
    )

    with create_engine("sqlite://").connect() as connection:
        assert connection.execute(statement).scalar_one() == expected


def test_filtered_issue_scan_does_not_probe_reports_before_page_limit() -> None:
    statement = _authorized_issues("default", {"cluster-1"})
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "rca_reports" not in sql
    assert "row_number()" in sql


def test_latest_report_summary_is_one_bounded_batch_without_internal_action() -> None:
    correlation_ids = [f"correlation-{index}" for index in range(500)]
    statement = latest_rca_issue_report_summaries_statement(
        "default",
        correlation_ids,
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    literal_sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "rca_reports" in sql
    assert "DISTINCT ON" in sql
    assert "executive_summary" in literal_sql
    assert "recommended_action" in literal_sql
    assert "evidence_bundle_summary" in literal_sql
    assert "supporting_evidence" in literal_sql
    assert "missing_evidence" in literal_sql
    assert "rca_issue_report_summary" in literal_sql
    assert "rca_reports.action" not in sql
    assert len(compiled.params["correlation_id_1"]) == MAX_RCA_REPORT_SUMMARY_BATCH


def test_recommended_action_projection_never_uses_internal_workflow_tokens() -> None:
    projection = issue_detail_projection(
        {
            "action": "plan_recovery",
            "recommendation": "user_selection_required",
            "details": {
                "plan": {
                    "candidates": [
                        {
                            "description": "문제 배포를 직전 안정 버전으로 되돌리세요.",
                            "title": "배포 롤백",
                        }
                    ]
                }
            },
        }
    )

    assert projection["recommended_action_summary"] == "배포 롤백"
    assert ("action",) not in RECOMMENDED_ACTION_SUMMARY_PATHS
    assert ("recommendation",) not in RECOMMENDED_ACTION_SUMMARY_PATHS


def test_historical_confidence_cast_is_guarded_by_numeric_validation() -> None:
    statement = select(effective_confidence_column(RcaTimeline.__table__))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "CASE WHEN" in sql
    assert " ~ " in sql
    assert "CAST(" in sql


def test_issue_item_fills_nullable_detail_fields_from_report_projection() -> None:
    item = issue_item(
        {
            "workspace_id": "default",
            "correlation_id": "correlation-1",
            "cluster_id": "target-1",
            "incident_id": "incident-1",
            "current_subject": "rca.completed",
            "status": "rca_completed",
            "supporting_evidence": [
                "object://evidence/correlation-1.json#traces:cluster_recent_traces"
            ],
            "missing_evidence": [],
            "issue_severity": "warning",
            "severity_availability": "available",
            "severity_reason_code": None,
            "situation_summary": "timeline fallback",
            "recommended_action_summary": "timeline action fallback",
            "evidence_summary": "timeline evidence fallback",
            "evidence_bundle_summary": "timeline bundle fallback",
            "rca_issue_report_summary": {
                "executive_summary": "Tempo 지연 구간에서 오류 span이 확인됐습니다.",
                "recommended_action": "문제 배포를 직전 안정 버전으로 되돌리세요.",
                "evidence_summary": "오류 trace와 재시작 지표가 함께 증가했습니다.",
                "evidence_bundle_summary": "traces, metrics",
            },
        }
    )

    assert item.situation_summary == "Tempo 지연 구간에서 오류 span이 확인됐습니다."
    assert item.recommended_action_summary == "문제 배포를 직전 안정 버전으로 되돌리세요."
    assert item.evidence_summary == "오류 trace와 재시작 지표가 함께 증가했습니다."
    assert item.evidence_bundle_summary == "traces, metrics"


def test_issue_item_uses_timeline_payload_copy_when_report_does_not_exist() -> None:
    item = issue_item(
        {
            "workspace_id": "default",
            "correlation_id": "correlation-blocked",
            "cluster_id": "target-1",
            "incident_id": "incident-blocked",
            "current_subject": "approval.recommended",
            "status": "approval_recommended",
            "supporting_evidence": [],
            "missing_evidence": [],
            "issue_severity": "warning",
            "severity_availability": "available",
            "severity_reason_code": None,
            "situation_summary": "OOM 후보가 가장 높은 점수로 평가됐습니다.",
            "recommended_action_summary": "대상 워크로드 재시작",
            "evidence_summary": "Kubernetes 상태와 로그가 후보를 지지합니다.",
            "evidence_bundle_summary": "5개 provider 수집이 완료됐습니다.",
            "rca_issue_report_summary": None,
        }
    )

    assert item.situation_summary == "OOM 후보가 가장 높은 점수로 평가됐습니다."
    assert item.recommended_action_summary == "대상 워크로드 재시작"
    assert item.evidence_summary == "Kubernetes 상태와 로그가 후보를 지지합니다."
    assert item.evidence_bundle_summary == "5개 provider 수집이 완료됐습니다."


def test_issue_item_projects_latest_recovery_blocker_reason() -> None:
    item = issue_item(
        {
            "workspace_id": "default",
            "correlation_id": "correlation-blocked",
            "cluster_id": "target-1",
            "incident_id": "incident-blocked",
            "current_subject": "rca.action_required",
            "status": "action_required",
            "supporting_evidence": [],
            "missing_evidence": [],
            "issue_severity": "warning",
            "severity_availability": "available",
            "severity_reason_code": None,
            "recovery_reason_code": "gitops_authority_unavailable",
        }
    )

    assert item.recovery_reason_code == "gitops_authority_unavailable"


def test_issue_item_uses_completed_report_evidence_when_timeline_is_stale() -> None:
    item = issue_item(
        {
            "workspace_id": "default",
            "correlation_id": "correlation-oom",
            "cluster_id": "target-1",
            "incident_id": "incident-oom",
            "current_subject": "approval.recommended",
            "status": "approval_recommended",
            "supporting_evidence": [],
            "missing_evidence": ["signal:stale"],
            "issue_severity": "warning",
            "severity_availability": "available",
            "severity_reason_code": None,
            "rca_issue_report_summary": {
                "supporting_evidence": [
                    "kubernetes:cluster_resource_state",
                    "logs:related_logs",
                    "metrics:telemetry_metrics",
                ],
                "missing_evidence": [],
            },
        }
    )

    assert item.supporting_evidence == [
        "kubernetes:cluster_resource_state",
        "logs:related_logs",
        "metrics:telemetry_metrics",
    ]
    assert item.missing_evidence == []
