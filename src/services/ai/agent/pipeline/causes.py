from __future__ import annotations

from dataclasses import dataclass, field

from domains.rca.events import (
    EvidenceBundleBuiltBody,
    RcaActionRequiredBody,
    RcaAiFallbackRequestedBody,
    RcaAnalysisBlockedBody,
    RcaBacklogItemCreatedBody,
    RcaCandidatesEvaluatedBody,
    RcaCandidatesPlannedBody,
    RcaCompletedBody,
    RcaRuleMissing,
    RcaRuleMissingBody,
)
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.bodies.platform import PipelineContractFailedBody
from services.ai.agent.causes.engine import (
    NO_MATCHING_RULE_MESSAGE,
    analyze_root_cause,
    evaluate_causes,
    plan_causes,
)
from services.ai.agent.defaults import RcaDefaults, RcaMessages

BACKLOG_STATUS_OPEN = "open"


@dataclass(frozen=True)
class CausePlanner:
    ai_fallback_enabled: bool = True

    def plan_body(self, evt: EvidenceBundleBuiltBody) -> RcaCandidatesPlannedBody:
        plan = plan_causes(evt.incident, evt.evidence_bundle, evt.evidence.object_ref)
        return RcaCandidatesPlannedBody(
            candidate_count=plan.candidate_count,
            evidence_ref=evt.evidence.object_ref,
            candidates=plan.candidates,
            workspace_id=evt.evidence.workspace_id,
            evidence=evt.evidence,
            incident=evt.incident,
            evidence_bundle=evt.evidence_bundle,
            rule_missing=plan.rule_missing,
        )

    def plan_bodies(self, evt: EvidenceBundleBuiltBody) -> tuple[EventBody, ...]:
        planned = self.plan_body(evt)
        if planned.rule_missing is None:
            return (planned,)
        bodies: list[EventBody] = [
            RcaRuleMissingBody(rule_missing=planned.rule_missing, incident=evt.incident),
            build_backlog_item_created_body(planned.rule_missing, evt),
        ]
        if self.ai_fallback_enabled:
            bodies.append(build_ai_fallback_requested_body(planned.rule_missing, evt))
        bodies.append(planned)
        return tuple(bodies)


@dataclass(frozen=True)
class CauseEvaluator:
    messages: RcaMessages = field(default_factory=RcaMessages)

    def evaluate_body(self, evt: RcaCandidatesPlannedBody) -> EventBody:
        if evt.evidence_bundle is None:
            return PipelineContractFailedBody(
                contract="RcaCandidatesPlannedBody.evidence_bundle",
                reason=self.messages.missing_analysis_context,
                consumer="analyze-worker",
                payload=evt.to_body(),
                workspace_id=evt.workspace_id,
                evidence_ref=evt.evidence_ref,
                severity="warning",
                diagnostics={"reason_code": "context_missing"},
            )
        evaluations = evaluate_causes(evt.candidates, evt.evidence_bundle, evt.rule_missing)
        return RcaCandidatesEvaluatedBody(
            candidate_count=evt.candidate_count,
            evidence_ref=evt.evidence_ref,
            candidates=evt.candidates,
            evaluations=evaluations,
            workspace_id=evt.workspace_id,
            evidence=evt.evidence,
            incident=evt.incident,
            evidence_bundle=evt.evidence_bundle,
            rule_missing=evt.rule_missing,
        )


def block_reason_code(evt: RcaCandidatesEvaluatedBody, detail) -> str | None:
    if evt.rule_missing is not None or detail.root_cause == "unknown":
        return "rule_missing"
    if detail.root_cause == "insufficient_evidence":
        return "insufficient_evidence"
    if detail.selected_candidate_id == "none":
        return "no_evaluation"
    if detail.missing_evidence:
        return "insufficient_evidence"
    return None


@dataclass(frozen=True)
class RootCauseAnalyzer:
    defaults: RcaDefaults = field(default_factory=RcaDefaults)
    messages: RcaMessages = field(default_factory=RcaMessages)

    def complete_body(self, evt: RcaCandidatesEvaluatedBody) -> EventBody:
        if evt.evidence is None or evt.incident is None or evt.evidence_bundle is None:
            return RcaActionRequiredBody(
                reason=self.messages.missing_analysis_context,
                evidence_ref=evt.evidence_ref,
                workspace_id=evt.workspace_id,
            )
        rca_detail = analyze_root_cause(evt.evaluations)
        reason_code = block_reason_code(evt, rca_detail)
        if reason_code is not None:
            return RcaAnalysisBlockedBody(
                reason_code=reason_code,
                reason=rca_detail.reason,
                evidence_ref=evt.evidence_ref,
                workspace_id=evt.workspace_id,
                evidence=evt.evidence,
                incident=evt.incident,
                evidence_bundle=evt.evidence_bundle,
                candidates=evt.candidates,
                evaluations=evt.evaluations,
                rca_detail=rca_detail,
                rule_missing=evt.rule_missing,
                missing_evidence=rca_detail.missing_evidence,
                diagnostics={"root_cause": rca_detail.root_cause},
            )
        return RcaCompletedBody(
            root_cause=rca_detail.root_cause,
            action=self.defaults.recommended_action,
            evidence_ref=evt.evidence_ref,
            workspace_id=evt.workspace_id,
            evidence=evt.evidence,
            incident=evt.incident,
            evidence_bundle=evt.evidence_bundle,
            candidates=evt.candidates,
            evaluations=evt.evaluations,
            rca_detail=rca_detail,
            rule_missing=evt.rule_missing,
        )


def build_backlog_item_created_body(
    rule_missing: RcaRuleMissing,
    evt: EvidenceBundleBuiltBody,
) -> RcaBacklogItemCreatedBody:
    backlog_id = f"missing-cause-rule:{rule_missing.workspace_id}:{rule_missing.symptom}"
    return RcaBacklogItemCreatedBody(
        backlog_id=backlog_id,
        title=f"RCA rule 추가 필요: {rule_missing.symptom}",
        reason=NO_MATCHING_RULE_MESSAGE,
        evidence_ref=rule_missing.evidence_ref,
        incident_id=rule_missing.incident_id,
        symptom=rule_missing.symptom,
        missing_evidence=rule_missing.missing_evidence,
        status=BACKLOG_STATUS_OPEN,
        payload={
            "incident": evt.incident.to_body(),
            "evidence_bundle": evt.evidence_bundle.to_body(),
            "rule_missing": rule_missing.to_body(),
        },
        workspace_id=rule_missing.workspace_id,
    )


def build_ai_fallback_requested_body(
    rule_missing: RcaRuleMissing,
    evt: EvidenceBundleBuiltBody,
) -> RcaAiFallbackRequestedBody:
    return RcaAiFallbackRequestedBody(
        reason=NO_MATCHING_RULE_MESSAGE,
        evidence_ref=rule_missing.evidence_ref,
        incident=evt.incident,
        evidence_bundle=evt.evidence_bundle,
        missing_evidence=rule_missing.missing_evidence,
        workspace_id=rule_missing.workspace_id,
        evidence=evt.evidence,
    )
