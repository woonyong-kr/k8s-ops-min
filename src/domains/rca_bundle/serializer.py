"""저장된 RCA/recovery projection을 RemediationBundle 계약으로 재조합한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domains.rca.report_projection import rca_report_summary
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.responses import (
    RcaReportSummaryItem,
    RemediationBundleDiagnosis,
    RemediationBundleMeta,
    RemediationBundleRecoveryCandidate,
    RemediationBundleRemediation,
    RemediationBundleResponse,
)


def remediation_bundle_response(
    report: JsonObject,
    recovery: JsonObject | None,
) -> RemediationBundleResponse:
    """최신 report와 선택적 recovery plan을 저장 없이 3계층으로 투영한다."""
    diagnosis_record = RcaReportSummaryItem.model_validate(rca_report_summary(report))
    cluster_id = _required_string(diagnosis_record.cluster_id, "report.cluster_id")

    remediation = None
    if recovery is not None:
        payload = recovery.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("recovery.payload must be an object")
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("recovery.payload.candidates must be a list")
        remediation = RemediationBundleRemediation(
            status=_required_string(recovery.get("status"), "recovery.status"),
            selected_action_id=_optional_string(recovery.get("selected_action_id")),
            selected_by=_optional_string(recovery.get("selected_by")),
            candidates=[
                RemediationBundleRecoveryCandidate.model_validate(candidate)
                for candidate in raw_candidates
            ],
            evidence_ref=_required_string(recovery.get("evidence_ref"), "recovery.evidence_ref"),
        )

    return RemediationBundleResponse(
        meta=RemediationBundleMeta(
            correlation_id=diagnosis_record.correlation_id,
            incident_id=diagnosis_record.incident_id,
            cluster_id=cluster_id,
            workspace_id=diagnosis_record.workspace_id,
            created_at=diagnosis_record.created_at,
        ),
        diagnosis=RemediationBundleDiagnosis(
            root_cause=diagnosis_record.root_cause,
            confidence=diagnosis_record.confidence,
            supporting_evidence=diagnosis_record.supporting_evidence,
            missing_evidence=diagnosis_record.missing_evidence,
            supporting_evidence_refs=diagnosis_record.supporting_evidence_refs,
            missing_evidence_checks=diagnosis_record.missing_evidence_checks,
            selected_candidate_id=diagnosis_record.selected_candidate_id,
        ),
        remediation=remediation,
    )


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
