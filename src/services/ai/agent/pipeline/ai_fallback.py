"""AI fallback 원인 계획 — rule 미매칭 incident 를 LLM 후보로 바꿔 같은 평가 경로에 태움.

plan-worker 가 rule 미매칭 시 발행하는 `rca.ai_fallback.requested` 를 받아,
LLM 은 실제 catalog cause ID만 hypothesis로 제안한다. title/evidence/check/signal 계약은
catalog에서 복원하고 analyze-worker의 내용 기반 signal 평가를 통과하지 못하면 blocked 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from domains.rca.events import (
    CAUSE_CANDIDATE_SOURCE_AI_FALLBACK,
    CauseCandidate,
    RcaAiFallbackRequestedBody,
    RcaCandidatesPlannedBody,
)
from packages.ai.llm import LlmClient
from services.ai.agent.playbooks.cause import registered_cause_profiles

MAX_FALLBACK_CANDIDATES = 5
MAX_PROMPT_EVIDENCE_ITEMS = 20
MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0

# complete_json 이 프롬프트에 함께 싣는 JSON Schema — LLM 응답 형식 강제용.
FALLBACK_CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_FALLBACK_CANDIDATES,
            "items": {
                "type": "object",
                "properties": {
                    "cause_id": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["cause_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
}


def build_fallback_prompt(evt: RcaAiFallbackRequestedBody) -> str:
    """incident 증상 + evidence bundle 요약으로 원인 후보를 묻는 프롬프트.

    근거 원문(value)이 아니라 수집 당시의 summary 문자열만 싣는다 — 프롬프트 크기 제한과
    secret 원문 유출 방지를 겸한다.
    """
    incident = evt.incident
    lines = [
        "You are a Kubernetes incident root-cause analyst.",
        "No catalog RCA rule matched this incident. Propose plausible root-cause",
        "candidates for a downstream evidence-based evaluator. Do NOT declare a",
        "confirmed root cause — only candidates with the evidence needed to verify them.",
        "",
        "Incident:",
        f"- symptom: {incident.symptom}",
        f"- severity: {incident.severity}",
        f"- resource: {incident.resource_kind}/{incident.resource_name}"
        f" (namespace: {incident.namespace or 'unknown'})",
        f"- summary: {incident.summary}",
        "",
        "Collected evidence summaries:",
    ]
    items = evt.evidence_bundle.items[:MAX_PROMPT_EVIDENCE_ITEMS]
    if items:
        lines.extend(f"- [{item.source}] {item.name}: {item.summary}" for item in items)
    else:
        lines.append("- (none)")
    if evt.missing_evidence:
        lines.extend(["", f"Missing evidence sources: {', '.join(evt.missing_evidence)}"])
    lines.extend(
        [
            "",
            "Choose hypotheses only from these catalog cause IDs:",
            ", ".join(_catalog_candidates()),
            "",
            f"Return at most {MAX_FALLBACK_CANDIDATES} candidates as JSON:",
            '{"candidates": [{"cause_id", "confidence"}]}',
            "Do not invent cause IDs or evidence requirements.",
        ]
    )
    return "\n".join(lines)


def parse_fallback_candidates(
    raw: Any, max_candidates: int = MAX_FALLBACK_CANDIDATES
) -> list[CauseCandidate]:
    """LLM JSON 응답 → CauseCandidate 목록.

    비정형·catalog 밖 응답은 예외 대신 빈 목록으로 수렴시킨다(항목 단위 방어).
    LLM 텍스트는 버리고 catalog의 evidence/check/signal 계약을 복원한다. 내용 기반 signal이
    없는 catalog 후보도 fallback 확정 경로에 태우지 않는다. confidence는 정렬에만 쓴다.
    """
    entries = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []
    catalog = _catalog_candidates()
    scored: list[tuple[float, CauseCandidate]] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        candidate_id = _clean_text(entry.get("cause_id") or entry.get("candidate_id"))
        candidate = catalog.get(candidate_id)
        if candidate is None or candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        scored.append(
            (
                _clamped_confidence(entry.get("confidence")),
                CauseCandidate(
                    candidate_id=candidate_id,
                    title=candidate.title,
                    description=candidate.description,
                    expected_evidence=list(candidate.expected_evidence),
                    checks=list(candidate.checks),
                    signals=[dict(group) for group in candidate.signals],
                    source=CAUSE_CANDIDATE_SOURCE_AI_FALLBACK,
                ),
            )
        )
    scored.sort(key=lambda pair: -pair[0])
    return [candidate for _, candidate in scored[:max_candidates]]


@dataclass(frozen=True)
class AiFallbackPlanner:
    """rca.ai_fallback.requested → rca.candidates.planned (source=ai_fallback)."""

    max_candidates: int = MAX_FALLBACK_CANDIDATES

    async def plan_body(
        self,
        evt: RcaAiFallbackRequestedBody,
        llm: LlmClient,
    ) -> RcaCandidatesPlannedBody | None:
        """유효 후보가 없으면 None — 호출자는 아무 이벤트도 내지 않는다.

        LLM 미설정(ValueError)·HTTP 실패·JSON 파싱 실패는 예외로 전파되고,
        worker 가 로그만 남기고 종료한다.
        """
        raw = await llm.complete_json(build_fallback_prompt(evt), FALLBACK_CANDIDATES_SCHEMA)
        candidates = parse_fallback_candidates(raw, self.max_candidates)
        if not candidates:
            return None
        return RcaCandidatesPlannedBody(
            candidate_count=len(candidates),
            evidence_ref=evt.evidence_ref,
            candidates=candidates,
            workspace_id=evt.workspace_id,
            evidence=evt.evidence,
            incident=evt.incident,
            evidence_bundle=evt.evidence_bundle,
            # rule_missing 을 비워야 analyze-worker 가 unknown 고정 평가 대신
            # 근거 매칭 평가를 수행한다(rule 경로와 동일 판정 기준).
            rule_missing=None,
        )


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _catalog_candidates() -> dict[str, CauseCandidate]:
    """내용 signal을 가진 실제 catalog 후보만 ID로 색인한다."""
    candidates: dict[str, CauseCandidate] = {}
    for profile in registered_cause_profiles():
        for spec in profile.candidate_specs:
            if not spec.signals:
                continue
            candidates.setdefault(spec.candidate_id, spec.to_candidate())
    return candidates


def _clamped_confidence(value: Any) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return MIN_CONFIDENCE
    return min(max(float(value), MIN_CONFIDENCE), MAX_CONFIDENCE)
