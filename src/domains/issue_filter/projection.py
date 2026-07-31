"""Issues 필터용 event-time label projection.

현재 inventory를 다시 읽어 과거 장애에 붙이지 않는다. 저장된 evidence snapshot에서
정확히 한 리소스가 식별될 때만 bounded label projection을 반환한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

MAX_ISSUE_LABELS = 12
ISSUE_EVIDENCE_KIND = "rca_bundle"


def extract_issue_evidence_labels(
    evidence: Mapping[str, Any],
    *,
    cluster_id: str,
    namespace: str | None,
    resource_kind: str,
    resource_name: str,
) -> JsonObject:
    """정확한 event-time resource 한 건의 labels만 fail-closed로 투영한다."""
    if _clean_string(evidence.get("cluster_id")) != _clean_string(cluster_id):
        return _incomplete("target_not_found")

    kubernetes = evidence.get("kubernetes")
    if not isinstance(kubernetes, Mapping):
        return _incomplete("target_not_found")
    workloads = kubernetes.get("workloads")
    if not isinstance(workloads, list):
        return _incomplete("target_not_found")

    matches = [
        item
        for item in workloads
        if isinstance(item, Mapping)
        and _same_identity(
            item,
            namespace=namespace,
            resource_kind=resource_kind,
            resource_name=resource_name,
        )
    ]
    if not matches:
        return _incomplete("target_not_found")
    if len(matches) != 1:
        return _incomplete("target_ambiguous")

    resource = matches[0]
    if "labels_complete" not in resource or not isinstance(resource["labels_complete"], bool):
        return _incomplete("source_labels_completeness_unknown")

    labels = _bounded_labels(resource.get("labels"))
    source_complete = resource["labels_complete"] is True
    bounded_complete = source_complete and len(labels) == _label_count(resource.get("labels"))
    reason_code = None
    if not source_complete:
        reason_code = "source_labels_incomplete"
    elif not bounded_complete:
        reason_code = "projection_label_limit"
    return {
        "labels": labels,
        "labels_complete": bounded_complete,
        "reason_code": reason_code,
    }


def _same_identity(
    item: Mapping[str, Any],
    *,
    namespace: str | None,
    resource_kind: str,
    resource_name: str,
) -> bool:
    return (
        _clean_string(item.get("kind")).casefold() == _clean_string(resource_kind).casefold()
        and _clean_string(item.get("namespace")) == _clean_string(namespace)
        and _clean_string(item.get("name")) == _clean_string(resource_name)
    )


def _bounded_labels(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    normalized = sorted(
        (
            (_clean_string(key), label_value.strip())
            for key, label_value in value.items()
            if _clean_string(key) and isinstance(label_value, str)
        ),
        key=lambda item: item[0],
    )
    return dict(normalized[:MAX_ISSUE_LABELS])


def _label_count(value: object) -> int:
    if not isinstance(value, Mapping):
        return 0
    return sum(
        1
        for key, label_value in value.items()
        if _clean_string(key) and isinstance(label_value, str)
    )


def _clean_string(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _incomplete(reason_code: str) -> JsonObject:
    return {"labels": {}, "labels_complete": False, "reason_code": reason_code}
