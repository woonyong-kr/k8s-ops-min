"""RCA report 목록 응답용 projection.

원문 payload 는 감사·재처리용으로 보존하고, 목록 API 는 이 모듈에서 만든 작은
화이트리스트 필드만 읽는다. 저장소와 라우터가 같은 규칙을 공유해 스키마 drift 를 막는다.
"""

from __future__ import annotations

from typing import Any

from domains.rca.report_narrative import (
    RCA_NARRATIVE_GENERATED,
    RCA_NARRATIVE_PAYLOAD_KEY,
    RCA_NARRATIVE_STATUS_KEY,
    RCA_NARRATIVE_UNAVAILABLE,
    normalize_rca_narrative,
)
from packages.contracts.event_bus.interfaces import JsonObject


def rca_report_projection(payload: JsonObject) -> JsonObject:
    """payload 원문에서 외부 노출 가능한 RCA 요약 필드만 뽑는다."""
    incident = payload.get("incident") if isinstance(payload.get("incident"), dict) else {}
    detail = payload.get("rca_detail") if isinstance(payload.get("rca_detail"), dict) else {}
    narrative = normalize_rca_narrative(payload.get(RCA_NARRATIVE_PAYLOAD_KEY))
    return {
        "analysis_status": _analysis_status(payload),
        "incident_id": incident.get("incident_id"),
        "cluster_id": incident.get("cluster_id"),
        "symptom": incident.get("symptom"),
        "severity": incident.get("severity"),
        "first_seen_at": incident.get("first_seen_at"),
        "confidence": detail.get("confidence"),
        "reason": detail.get("reason"),
        "evidence_ref": payload.get("evidence_ref"),
        "supporting_evidence": _str_list(detail.get("supporting_evidence")),
        "missing_evidence": _str_list(detail.get("missing_evidence")),
        "evidence_summary": _optional_str(detail.get("evidence_summary")),
        "evidence_bundle_summary": _optional_str(detail.get("evidence_bundle_summary")),
        "resource_kind": incident.get("resource_kind"),
        "resource_name": incident.get("resource_name"),
        "namespace": incident.get("namespace"),
        "secondary_symptoms": _str_list(incident.get("secondary_symptoms")),
        "selected_candidate_id": detail.get("selected_candidate_id"),
        "candidates": _candidate_scores(payload),
        "supporting_evidence_refs": _evidence_refs(detail.get("supporting_evidence_refs"), payload),
        "missing_evidence_checks": _missing_checks(detail.get("missing_evidence_checks")),
        RCA_NARRATIVE_PAYLOAD_KEY: narrative,
        RCA_NARRATIVE_STATUS_KEY: (
            RCA_NARRATIVE_GENERATED if narrative is not None else RCA_NARRATIVE_UNAVAILABLE
        ),
    }


def rca_report_summary(row: JsonObject) -> JsonObject:
    """RcaReport row → 원문 payload 없이도 만들 수 있는 화이트리스트 요약."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    projected = rca_report_projection(payload) if payload else {}

    def value(name: str, default: Any = None) -> Any:
        row_value = row.get(name)
        if row_value is not None:
            return row_value
        return projected.get(name, default)

    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "correlation_id": row["correlation_id"],
        "analysis_status": value("analysis_status", "completed"),
        "root_cause": row["root_cause"],
        "action": row["action"],
        "incident_id": value("incident_id"),
        "cluster_id": value("cluster_id"),
        "symptom": value("symptom"),
        "severity": value("severity"),
        "first_seen_at": value("first_seen_at"),
        "confidence": value("confidence"),
        "reason": value("reason"),
        "evidence_ref": value("evidence_ref"),
        "supporting_evidence": _str_list(value("supporting_evidence", [])),
        "missing_evidence": _str_list(value("missing_evidence", [])),
        "evidence_summary": _optional_str(value("evidence_summary")),
        "evidence_bundle_summary": _optional_str(value("evidence_bundle_summary")),
        "created_at": row.get("created_at"),
        "resource_kind": value("resource_kind"),
        "resource_name": value("resource_name"),
        "namespace": value("namespace"),
        "secondary_symptoms": _str_list(value("secondary_symptoms", [])),
        "selected_candidate_id": value("selected_candidate_id"),
        "candidates": _object_list(value("candidates", [])),
        "supporting_evidence_refs": _object_list(value("supporting_evidence_refs", [])),
        "missing_evidence_checks": _object_list(value("missing_evidence_checks", [])),
        RCA_NARRATIVE_PAYLOAD_KEY: normalize_rca_narrative(value(RCA_NARRATIVE_PAYLOAD_KEY)),
        RCA_NARRATIVE_STATUS_KEY: _narrative_status(
            value(RCA_NARRATIVE_STATUS_KEY),
            value(RCA_NARRATIVE_PAYLOAD_KEY),
        ),
    }


def _analysis_status(payload: JsonObject) -> str:
    value = payload.get("analysis_status")
    return "blocked" if value == "blocked" else "completed"


def _narrative_status(value: Any, narrative: Any) -> str:
    if value == RCA_NARRATIVE_GENERATED and normalize_rca_narrative(narrative) is not None:
        return RCA_NARRATIVE_GENERATED
    return RCA_NARRATIVE_UNAVAILABLE


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _object_list(value: Any) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _candidate_scores(payload: JsonObject) -> list[JsonObject]:
    """candidates(카탈로그 메타) + evaluations(점수) 를 candidate_id 로 병합."""
    candidates = payload.get("candidates")
    evaluations = payload.get("evaluations")
    meta: dict[str, JsonObject] = {}
    for cand in candidates if isinstance(candidates, list) else []:
        if isinstance(cand, dict) and cand.get("candidate_id"):
            meta[str(cand["candidate_id"])] = cand
    items: list[JsonObject] = []
    for ev in evaluations if isinstance(evaluations, list) else []:
        if not (isinstance(ev, dict) and ev.get("candidate_id")):
            continue
        candidate_id = str(ev["candidate_id"])
        cand = meta.get(candidate_id, {})
        items.append(
            {
                "candidate_id": candidate_id,
                "title": cand.get("title"),
                "source": cand.get("source"),
                "score": ev.get("score"),
                "reason": ev.get("reason"),
                "supporting_evidence": _str_list(ev.get("supporting_evidence")),
                "missing_evidence": _str_list(ev.get("missing_evidence")),
            }
        )
    # 점수 내림차순 — 선정 후보가 항상 위로 온다.
    items.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return items


LINEAGE_KEY = "_lineage"
LINEAGE_STRING_FIELDS = (
    "source_version",
    "collector",
    "collector_version",
    "query_version",
    "collected_at",
    "evidence_key",
    "source_id",
    "agent_id",
    "window_start",
)


def _evidence_refs(value: Any, payload: JsonObject | None = None) -> list[JsonObject]:
    lineage_by_key = _lineage_by_reference(payload or {})
    refs: list[JsonObject] = []
    for ref in value if isinstance(value, list) else []:
        if not (isinstance(ref, dict) and ref.get("source") and ref.get("name")):
            continue
        item = {
            "source": str(ref["source"]),
            "name": str(ref["name"]),
            "check_id": ref.get("check_id") or None,
            "summary": ref.get("summary") or None,
            "query": ref.get("query") or None,
            "evidence_ref": ref.get("evidence_ref") or None,
        }
        lineage = {
            **_lineage_fields(ref),
            **_lineage_for_reference(item, lineage_by_key),
        }
        item.update(lineage)
        refs.append(item)
    return refs


def _lineage_by_reference(payload: JsonObject) -> dict[str, JsonObject]:
    bundle = payload.get("evidence_bundle")
    items = bundle.get("items") if isinstance(bundle, dict) else None
    out: dict[str, JsonObject] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        lineage = _lineage_from_value(item.get("value"))
        if not lineage:
            continue
        keys = [
            item.get("evidence_ref"),
            item.get("check_id"),
        ]
        if item.get("source") and item.get("name"):
            keys.append(f"{item['source']}/{item['name']}")
        for key in keys:
            if key:
                out[str(key)] = lineage
    return out


def _lineage_from_value(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    lineage = value.get(LINEAGE_KEY)
    return _lineage_fields(lineage) if isinstance(lineage, dict) else {}


def _lineage_for_reference(ref: JsonObject, lineage_by_key: dict[str, JsonObject]) -> JsonObject:
    keys = [
        ref.get("evidence_ref"),
        ref.get("check_id"),
        f"{ref['source']}/{ref['name']}" if ref.get("source") and ref.get("name") else None,
    ]
    for key in keys:
        if key and str(key) in lineage_by_key:
            return lineage_by_key[str(key)]
    return {}


def _lineage_fields(raw: Any) -> JsonObject:
    if not isinstance(raw, dict):
        return {}
    out: JsonObject = {}
    schema_version = raw.get("schema_version")
    if schema_version is not None:
        try:
            out["schema_version"] = int(schema_version)
        except (TypeError, ValueError):
            pass
    for field in LINEAGE_STRING_FIELDS:
        value = raw.get(field)
        if value not in (None, ""):
            out[field] = str(value)
    return out


def _missing_checks(value: Any) -> list[JsonObject]:
    checks: list[JsonObject] = []
    for check in value if isinstance(value, list) else []:
        if not (isinstance(check, dict) and check.get("check_id")):
            continue
        checks.append(
            {
                "check_id": str(check["check_id"]),
                "source": check.get("source"),
                "status": check.get("status"),
                "reason": check.get("reason"),
            }
        )
    return checks
