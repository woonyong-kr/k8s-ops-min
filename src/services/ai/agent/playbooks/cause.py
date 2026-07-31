from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from domains.rca.events import CauseCandidate, EvidenceBundle, IncidentRecord
from packages.contracts.event_bus.bodies import JsonObject


class EvidenceRequirementRule(Protocol):
    def matches(self, incident: IncidentRecord) -> bool: ...

    def required_sources(self, incident: IncidentRecord) -> list[str]: ...


class CauseRule(Protocol):
    def matches(self, incident: IncidentRecord, evidence_bundle: EvidenceBundle) -> bool: ...

    def candidates(
        self,
        incident: IncidentRecord,
        evidence_bundle: EvidenceBundle,
    ) -> list[CauseCandidate]: ...


@dataclass(frozen=True)
class CauseCandidateSpec:
    candidate_id: str
    title: str
    description: str
    expected_evidence: tuple[str, ...]
    checks: tuple[str, ...]
    # 판별 신호 그룹 — {"id": str, "any_of": [{"fact"|"log_pattern"|"event_pattern": str}...]}.
    # 스키마 검증은 카탈로그 로더(causes/loader.py)가, 평가는 causes/signals.py 가 담당한다.
    signals: tuple[JsonObject, ...] = ()

    def to_candidate(self) -> CauseCandidate:
        return CauseCandidate(
            candidate_id=self.candidate_id,
            title=self.title,
            description=self.description,
            expected_evidence=list(self.expected_evidence),
            checks=list(self.checks),
            signals=[dict(group) for group in self.signals],
        )


@dataclass(frozen=True)
class SymptomEvidenceRequirementRule:
    symptoms: tuple[str, ...]
    sources: tuple[str, ...]

    def matches(self, incident: IncidentRecord) -> bool:
        return incident.symptom in self.symptoms

    def required_sources(self, incident: IncidentRecord) -> list[str]:
        return list(self.sources)


@dataclass(frozen=True)
class SymptomCauseRule:
    symptoms: tuple[str, ...]
    candidate_specs: tuple[CauseCandidateSpec, ...]

    def matches(self, incident: IncidentRecord, evidence_bundle: EvidenceBundle) -> bool:
        return incident.symptom in self.symptoms

    def candidates(
        self,
        incident: IncidentRecord,
        evidence_bundle: EvidenceBundle,
    ) -> list[CauseCandidate]:
        return [candidate.to_candidate() for candidate in self.candidate_specs]


@dataclass(frozen=True)
class CauseProfile:
    symptoms: tuple[str, ...]
    required_sources: tuple[str, ...]
    candidate_specs: tuple[CauseCandidateSpec, ...]
    # 룰 식별자 — YAML 카탈로그 룰은 필수, 코드 정의 룰은 선택(None 이면 중복 검사 제외).
    rule_id: str | None = None

    def evidence_rule(self) -> SymptomEvidenceRequirementRule:
        return SymptomEvidenceRequirementRule(self.symptoms, self.required_sources)

    def cause_rule(self) -> SymptomCauseRule:
        return SymptomCauseRule(self.symptoms, self.candidate_specs)


CAUSE_PROFILES: list[CauseProfile] = []


def causes_for(
    *,
    symptoms: tuple[str, ...],
    required_sources: tuple[str, ...],
    candidates: tuple[CauseCandidateSpec, ...],
    rule_id: str | None = None,
) -> Callable[[type], type]:
    def decorator(marker: type) -> type:
        if rule_id is not None and any(p.rule_id == rule_id for p in CAUSE_PROFILES):
            raise ValueError(f"RCA 룰 id 중복: '{rule_id}' — 코드 룰 id 는 고유해야 합니다.")
        CAUSE_PROFILES.append(CauseProfile(symptoms, required_sources, candidates, rule_id))
        return marker

    return decorator


def registered_cause_profiles() -> tuple[CauseProfile, ...]:
    import services.ai.agent.causes.catalog  # noqa: F401

    return tuple(CAUSE_PROFILES)


def evidence_rules(
    profiles: tuple[CauseProfile, ...] | None = None,
) -> tuple[EvidenceRequirementRule, ...]:
    active_profiles = registered_cause_profiles() if profiles is None else profiles
    return tuple(profile.evidence_rule() for profile in active_profiles)


def cause_rules(
    profiles: tuple[CauseProfile, ...] | None = None,
) -> tuple[CauseRule, ...]:
    active_profiles = registered_cause_profiles() if profiles is None else profiles
    return tuple(profile.cause_rule() for profile in active_profiles)
