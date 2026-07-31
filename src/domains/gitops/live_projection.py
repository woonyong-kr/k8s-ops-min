"""연결 시점 desired(git) vs live(cluster) 프리뷰의 순수 계산.

클러스터 에이전트가 이미 관측해 저장한 inventory `raw` 요약(workload 의
desired_replicas·pod_template, service 의 type·selector·ports 등)에서 diffing.py
가 쓰는 형태의 live Kubernetes 오브젝트를 재구성하고, 렌더된 desired 오브젝트와
관리필드(managed-field) 단위로 비교해 리소스별 변경을 분류한다.

여기서는 DB·네트워크에 손대지 않는다(순수 함수) — 조회는 저장소가, 렌더는
discovery 가, 오케스트레이션은 서비스가 담당한다. 이렇게 나눠야 프리뷰의 diff 의미가
실제 리컨사일(diff-worker)과 '동일한' diffing.py 엔진을 공유해 모순이 없다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domains.gitops.diffing import (
    MISSING,
    compare_managed_fields,
    extract_declared_field_paths,
    extract_managed_fields,
    snapshot_from_kubernetes_object,
    summarize_status,
)

JsonObject = dict[str, Any]

# diffing.extract_managed_fields 가 필드 단위로 모델링하는 kind. 그 외 kind 는
# live 존재 여부(생성/유지)만 판정하고 필드 diff 는 생성하지 않는다(정직하게 미지원).
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

# 리소스 변경 분류(프리뷰 표기)
CHANGE_CREATE = "create"  # 클러스터에 없음 → 새로 생성/적용 예정
CHANGE_UPDATE = "update"  # 있으나 관리필드가 달라 변경 예정
CHANGE_IN_SYNC = "in_sync"  # 있고 관리필드 일치 → 그대로 유지


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def resource_ref(kind: str, name: str) -> str:
    """inventory 조회 키. get_actual_resource_image 과 동일 규약("kind/name", kind 소문자)."""
    return f"{str(kind).strip().lower()}/{str(name).strip()}"


def reconstruct_live_object(kind: str, raw: Mapping[str, Any] | None) -> JsonObject | None:
    """관측 저장분(inventory raw 요약)에서 diffing 용 live k8s 오브젝트를 재구성.

    관측된 필드가 없으면 None(=live 미관측). 재구성은 diffing.extract_managed_fields
    가 읽는 경로(spec.replicas, spec.template.spec.containers[...], service spec)를
    채우는 데 필요한 만큼만 만든다.
    """
    raw_map = _mapping(raw)
    resolved_kind = str(kind or raw_map.get("kind") or "").strip()
    if not resolved_kind:
        return None
    name = raw_map.get("name")
    namespace = raw_map.get("namespace")
    metadata: JsonObject = {}
    if name is not None:
        metadata["name"] = name
    if namespace is not None:
        metadata["namespace"] = namespace

    if resolved_kind in WORKLOAD_KINDS:
        spec: JsonObject = {}
        if raw_map.get("desired_replicas") is not None:
            spec["replicas"] = raw_map.get("desired_replicas")
        template = raw_map.get("pod_template")
        if isinstance(template, Mapping) and template:
            spec["template"] = dict(template)
        if not spec:
            return None
        return {
            "apiVersion": str(raw_map.get("api_version") or "apps/v1"),
            "kind": resolved_kind,
            "metadata": metadata,
            "spec": spec,
        }

    if resolved_kind == "Service":
        spec = {}
        for key in ("type", "selector", "ports"):
            if raw_map.get(key) is not None:
                spec[key] = raw_map.get(key)
        if not spec:
            return None
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": spec,
        }

    if resolved_kind == "ConfigMap":
        payload: JsonObject = {}
        for key in ("data", "binaryData"):
            if raw_map.get(key) is not None:
                payload[key] = raw_map.get(key)
        if not payload:
            return None
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata,
            **payload,
        }

    # 커스텀/동적 리소스: 에이전트가 apiVersion/kind/metadata/spec/status 형태로
    # 저장한 경우 그대로 매니페스트로 취급(필드 diff 는 kind 미지원이라 존재 판정만).
    if isinstance(raw_map.get("spec"), Mapping):
        return {
            "apiVersion": str(raw_map.get("apiVersion") or raw_map.get("api_version") or ""),
            "kind": resolved_kind,
            "metadata": _mapping(raw_map.get("metadata")) or metadata,
            "spec": dict(raw_map["spec"]),
        }
    return None


def project_resource_diff(
    desired: Mapping[str, Any],
    live: Mapping[str, Any] | None,
) -> JsonObject:
    """desired(렌더된 git 오브젝트) vs live(재구성) 를 관리필드 단위로 비교.

    반환: {change, status, field_changes}. field_changes 는 desired 가 선언한 필드
    중 live 와 다른 것만(before/after). live 가 None 이면 change="create".
    old_desired 는 없음(연결 최초=adoption) 이므로 diffing 의 adoption 의미를 그대로 쓴다.
    """
    new_desired_fields = extract_managed_fields(desired)
    declared = extract_declared_field_paths(desired)

    if live is None:
        return {
            "change": CHANGE_CREATE,
            "status": "adoption_required" if new_desired_fields else "no_change",
            "field_changes": [],
        }

    live_snapshot = snapshot_from_kubernetes_object(live, source="observed_live")
    changes = compare_managed_fields(
        old_desired=None,
        live=live_snapshot.fields,
        new_desired=new_desired_fields,
        managed_fields=declared or None,
    )
    field_changes: list[JsonObject] = []
    for change in changes:
        if str(change.get("classification")) != "adoption_required":
            continue
        field_changes.append(
            {
                "field_path": str(change.get("field_path")),
                "classification": str(change.get("classification")),
                "before": _display_value(change.get("live")),
                "after": _display_value(change.get("new_desired")),
            }
        )
    resource_change = CHANGE_UPDATE if field_changes else CHANGE_IN_SYNC
    return {
        "change": resource_change,
        "status": summarize_status(changes),
        "field_changes": field_changes,
    }


def _display_value(value: Any) -> str:
    """프리뷰 표기용 문자열. 누락은 '<missing>', 복합값은 안정적 요약."""
    if value is MISSING:
        return MISSING
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if value is None:
        return "null"
    return _stable_repr(value)


def _stable_repr(value: Any) -> str:
    import json

    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)
