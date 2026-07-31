from __future__ import annotations

from dataclasses import dataclass

from domains.rca.events import (
    CauseCandidate,
    CauseEvaluation,
    EvidenceBundle,
    EvidenceReference,
    IncidentRecord,
    MissingEvidenceCheck,
    RcaReportDetail,
    RcaRuleMissing,
)
from services.ai.agent.causes.catalog import cause_rules, evidence_rules
from services.ai.agent.causes.signals import (
    describe_signal_group,
    extract_bundle_signals,
    signal_missing_token,
    split_signal_groups,
)
from services.ai.agent.playbooks.cause import CauseRule, EvidenceRequirementRule

DEFAULT_REQUIRED_EVIDENCE = ["kubernetes"]
MATCHING_CAUSE_RULE_EVIDENCE = "matching_cause_rule"
NO_MATCHING_RULE_MESSAGE = "정의된 RCA rule 없음"
UNKNOWN_EVALUATION_ID = "unknown"


@dataclass(frozen=True)
class CausePlan:
    candidates: list[CauseCandidate]
    rule_missing: RcaRuleMissing | None = None

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


def unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def required_evidence_sources(
    incident: IncidentRecord,
    rules: tuple[EvidenceRequirementRule, ...] | None = None,
) -> list[str]:
    active_rules = evidence_rules() if rules is None else rules
    sources: list[str] = []
    for rule in active_rules:
        if rule.matches(incident):
            sources.extend(rule.required_sources(incident))
    return unique_ordered(sources) if sources else list(DEFAULT_REQUIRED_EVIDENCE)


def merge_candidates(candidates: list[CauseCandidate]) -> list[CauseCandidate]:
    by_id: dict[str, CauseCandidate] = {}
    for candidate in candidates:
        existing = by_id.get(candidate.candidate_id)
        if existing is None:
            by_id[candidate.candidate_id] = candidate
            continue
        by_id[candidate.candidate_id] = CauseCandidate(
            candidate_id=existing.candidate_id,
            title=existing.title,
            description=existing.description,
            expected_evidence=unique_ordered(
                existing.expected_evidence + candidate.expected_evidence
            ),
            checks=unique_ordered(existing.checks + candidate.checks),
            signals=merge_signal_groups(existing.signals, candidate.signals),
            source=existing.source,
        )
    return list(by_id.values())


def merge_signal_groups(existing: list, incoming: list) -> list:
    """signals 병합 — 그룹 id 기준 중복 제거(순서 보존, 먼저 등록된 정의 유지)."""
    merged = list(existing)
    seen_ids = {str(group.get("id")) for group in existing if isinstance(group, dict)}
    for group in incoming:
        group_id = str(group.get("id")) if isinstance(group, dict) else None
        if group_id is not None and group_id in seen_ids:
            continue
        if group_id is not None:
            seen_ids.add(group_id)
        merged.append(group)
    return merged


def build_rule_missing(incident: IncidentRecord, evidence_ref: str) -> RcaRuleMissing:
    return RcaRuleMissing(
        incident_id=incident.incident_id,
        symptom=incident.symptom,
        evidence_ref=evidence_ref,
        missing_evidence=[MATCHING_CAUSE_RULE_EVIDENCE],
        message=NO_MATCHING_RULE_MESSAGE,
        workspace_id=incident.workspace_id,
    )


def plan_causes(
    incident: IncidentRecord,
    evidence_bundle: EvidenceBundle,
    evidence_ref: str,
    rules: tuple[CauseRule, ...] | None = None,
) -> CausePlan:
    active_rules = cause_rules() if rules is None else rules
    candidates: list[CauseCandidate] = []
    for rule in active_rules:
        if rule.matches(incident, evidence_bundle):
            candidates.extend(rule.candidates(incident, evidence_bundle))

    merged = merge_candidates(candidates)
    if merged:
        return CausePlan(candidates=merged)
    return CausePlan(candidates=[], rule_missing=build_rule_missing(incident, evidence_ref))


def evaluate_causes(
    candidates: list[CauseCandidate],
    evidence_bundle: EvidenceBundle,
    rule_missing: RcaRuleMissing | None = None,
) -> list[CauseEvaluation]:
    if rule_missing is not None:
        return [
            CauseEvaluation(
                candidate_id=UNKNOWN_EVALUATION_ID,
                score=0.0,
                checks=[MATCHING_CAUSE_RULE_EVIDENCE],
                supporting_evidence=[],
                missing_evidence=rule_missing.missing_evidence,
                reason=NO_MATCHING_RULE_MESSAGE,
            )
        ]

    actual_evidence = evidence_keys(evidence_bundle)
    bundle_signals = extract_bundle_signals(evidence_bundle)
    evaluations: list[CauseEvaluation] = []
    for candidate in candidates:
        expected = set(candidate.expected_evidence)
        supporting = sorted(expected & actual_evidence)
        missing_sources = sorted(expected - actual_evidence)
        # 판별 신호(내용 매칭) — 소스 존재만으로 점수가 1.0 이 되는 오판을 막는다.
        # 선언된 그룹이 하나라도 미충족이면 missing_evidence 에 signal 토큰이 남아
        # 해당 후보가 선택되더라도 완결(rca.completed)이 아니라 blocked 로 흐른다.
        matched_groups, unmatched_groups = split_signal_groups(candidate.signals, bundle_signals)
        missing = missing_sources + [signal_missing_token(group) for group in unmatched_groups]
        denominator = len(expected) + len(candidate.signals)
        score = (len(supporting) + len(matched_groups)) / denominator if denominator else 0.0
        evaluations.append(
            CauseEvaluation(
                candidate_id=candidate.candidate_id,
                score=score,
                checks=candidate.checks,
                supporting_evidence=supporting,
                missing_evidence=missing,
                reason=build_evaluation_reason(
                    expected_count=len(expected),
                    supporting_count=len(supporting),
                    signal_count=len(candidate.signals),
                    matched_signal_count=len(matched_groups),
                ),
                supporting_evidence_refs=evidence_refs_for_keys(evidence_bundle, supporting),
                missing_evidence_checks=[
                    *missing_evidence_checks(missing_sources, candidate.checks),
                    *missing_signal_checks(unmatched_groups),
                ],
                matched_signal_count=len(matched_groups),
                required_signal_count=len(candidate.signals),
            )
        )
    return evaluations


def build_evaluation_reason(
    *,
    expected_count: int,
    supporting_count: int,
    signal_count: int,
    matched_signal_count: int,
) -> str:
    reason = f"필요한 근거 {expected_count}개 중 {supporting_count}개가 수집되었습니다."
    if signal_count:
        reason += f" 판별 신호 {signal_count}개 중 {matched_signal_count}개가 확인되었습니다."
    return reason


def missing_signal_checks(unmatched_groups: list) -> list[MissingEvidenceCheck]:
    return [
        MissingEvidenceCheck(
            check_id=signal_missing_token(group),
            source="signals",
            status="missing",
            reason=f"판별 신호 미충족 — 다음 중 하나가 관측되어야 합니다: {describe_signal_group(group)}",
        )
        for group in unmatched_groups
    ]


def evidence_key(source: str, name: str) -> str:
    return f"{source}:{name}"


def evidence_keys(evidence_bundle: EvidenceBundle) -> set[str]:
    keys: set[str] = set()
    for item in evidence_bundle.items:
        keys.add(item.source)
        keys.add(evidence_key(item.source, item.name))
    return keys


def evidence_refs_for_keys(
    evidence_bundle: EvidenceBundle, keys: list[str]
) -> list[EvidenceReference]:
    key_set = set(keys)
    return [
        item.reference()
        for item in evidence_bundle.items
        if item.source in key_set or evidence_key(item.source, item.name) in key_set
    ]


def missing_evidence_checks(
    missing_evidence: list[str], candidate_checks: list[str] | None = None
) -> list[MissingEvidenceCheck]:
    checks = candidate_checks or []
    return [
        MissingEvidenceCheck(
            check_id=f"evidence:{source}:required",
            source=source,
            status="missing",
            reason=missing_evidence_reason(source, checks),
        )
        for source in missing_evidence
    ]


def missing_evidence_reason(source: str, candidate_checks: list[str]) -> str:
    if not candidate_checks:
        return f"{source} evidence query/check must complete before RCA can be finalized."
    checks = ", ".join(candidate_checks)
    return f"{source} evidence is required to evaluate checks: {checks}"


def build_root_cause_reason(selected: CauseEvaluation) -> str:
    if selected.candidate_id == UNKNOWN_EVALUATION_ID:
        return NO_MATCHING_RULE_MESSAGE
    return (
        f"{selected.candidate_id} 후보가 가장 높은 점수로 평가되었고, "
        f"누락 근거 {len(selected.missing_evidence)}개, "
        f"충족 근거 {len(selected.supporting_evidence)}개를 기준으로 최종 원인으로 선택했습니다."
    )


def unknown_root_cause(evaluation: CauseEvaluation) -> RcaReportDetail:
    return RcaReportDetail(
        root_cause=UNKNOWN_EVALUATION_ID,
        confidence=0.0,
        selected_candidate_id=UNKNOWN_EVALUATION_ID,
        supporting_evidence=[],
        missing_evidence=unique_ordered(
            [MATCHING_CAUSE_RULE_EVIDENCE, *evaluation.missing_evidence]
        ),
        reason=NO_MATCHING_RULE_MESSAGE,
        missing_evidence_checks=missing_evidence_checks(
            unique_ordered([MATCHING_CAUSE_RULE_EVIDENCE, *evaluation.missing_evidence])
        ),
        supporting_evidence_refs=evaluation.supporting_evidence_refs,
    )


def insufficient_evidence_root_cause(evaluations: list[CauseEvaluation]) -> RcaReportDetail:
    missing_evidence = unique_ordered(
        [evidence for evaluation in evaluations for evidence in evaluation.missing_evidence]
    )
    return RcaReportDetail(
        root_cause="insufficient_evidence",
        confidence=0.0,
        selected_candidate_id="none",
        supporting_evidence=[],
        missing_evidence=missing_evidence,
        reason="후보는 생성됐지만 매칭된 근거가 없어 최종 원인을 확정하지 않았습니다.",
        missing_evidence_checks=missing_evidence_checks(missing_evidence),
        supporting_evidence_refs=[],
    )


def analyze_root_cause(evaluations: list[CauseEvaluation]) -> RcaReportDetail:
    if not evaluations:
        return RcaReportDetail(
            root_cause="분석 가능한 원인 후보 없음",
            confidence=0.0,
            selected_candidate_id="none",
            supporting_evidence=[],
            missing_evidence=[],
            reason="평가된 원인 후보가 없어 최종 원인을 선택할 수 없습니다.",
            missing_evidence_checks=[],
            supporting_evidence_refs=[],
        )

    if evaluations[0].candidate_id == UNKNOWN_EVALUATION_ID:
        return unknown_root_cause(evaluations[0])

    discriminator_eligible = [
        evaluation
        for evaluation in evaluations
        if evaluation.required_signal_count == 0
        or evaluation.matched_signal_count == evaluation.required_signal_count
    ]
    supported = [
        evaluation
        for evaluation in discriminator_eligible
        if evaluation.supporting_evidence
    ]
    if not supported:
        return insufficient_evidence_root_cause(evaluations)

    selected = min(
        supported,
        key=lambda evaluation: (-evaluation.score, len(evaluation.missing_evidence)),
    )
    return RcaReportDetail(
        root_cause=selected.candidate_id,
        confidence=selected.score,
        selected_candidate_id=selected.candidate_id,
        supporting_evidence=selected.supporting_evidence,
        missing_evidence=selected.missing_evidence,
        reason=build_root_cause_reason(selected),
        missing_evidence_checks=selected.missing_evidence_checks,
        supporting_evidence_refs=selected.supporting_evidence_refs,
    )
