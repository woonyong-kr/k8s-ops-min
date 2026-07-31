"""managed-field GitOps diff 헬퍼.

"무엇을 비교할지" 정책을 워커와 분리 — 워커가 rendered manifest, SSA dry-run 출력,
live 객체를 넣으면 이 서비스가 관리하기로 한 필드만 남김.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

JsonObject = dict[str, Any]
MANAGED_FIELD_SOURCE = "managed-field-3way"
MISSING = "<missing>"


@dataclass(frozen=True)
class ManagedFieldSnapshot:
    resource: str
    namespace: str
    fields: JsonObject
    source: str


def resource_ref(kind: str, name: str) -> str:
    return f"{kind.lower()}/{name}"


def rendered_manifest_to_object(rendered: Any) -> JsonObject:
    """RenderedManifest 값 객체를 Kubernetes 객체 형태로 변환."""
    manifest = getattr(rendered, "manifest", None)
    if isinstance(manifest, Mapping) and manifest:
        return deepcopy(dict(manifest))

    rendered_object = getattr(rendered, "rendered_object", None)
    if isinstance(rendered_object, Mapping) and rendered_object:
        return deepcopy(dict(rendered_object))

    name = rendered.metadata.name
    namespace = rendered.metadata.namespace
    return {
        "apiVersion": rendered.api_version,
        "kind": rendered.kind,
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "replicas": rendered.spec.replicas,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": name,
                            "image": rendered.spec.image,
                        }
                    ]
                }
            },
        },
    }


def snapshot_from_rendered_manifest(
    rendered: Any, *, source: str = "rendered_manifest"
) -> ManagedFieldSnapshot:
    return snapshot_from_kubernetes_object(rendered_manifest_to_object(rendered), source=source)


def extract_declared_field_paths(obj: Mapping[str, Any]) -> list[str]:
    """렌더된 YAML에 명시적으로 존재한 정책 후보 경로 반환."""
    return sorted(extract_managed_fields(obj))


def snapshot_from_kubernetes_object(obj: Mapping[str, Any], *, source: str) -> ManagedFieldSnapshot:
    metadata = _mapping(obj.get("metadata"))
    kind = str(obj.get("kind", "Unknown"))
    name = str(metadata.get("name", "unknown"))
    namespace = str(metadata.get("namespace", "default"))
    return ManagedFieldSnapshot(
        resource=resource_ref(kind, name),
        namespace=namespace,
        fields=extract_managed_fields(obj),
        source=source,
    )


def extract_managed_fields(obj: Mapping[str, Any]) -> JsonObject:
    kind = str(obj.get("kind", ""))
    if kind == "Deployment":
        return _deployment_fields(obj)
    if kind == "Service":
        return _service_fields(obj)
    if kind == "ConfigMap":
        return _configmap_fields(obj)
    return {}


def compare_managed_fields(
    *,
    old_desired: Mapping[str, Any] | None,
    live: Mapping[str, Any],
    new_desired: Mapping[str, Any],
    managed_fields: Iterable[str] | None = None,
    ignored_fields: Iterable[str] | None = None,
) -> list[JsonObject]:
    old_desired = apply_field_policy(old_desired, managed_fields, ignored_fields)
    live = apply_field_policy(live, managed_fields, ignored_fields)
    new_desired = apply_field_policy(new_desired, managed_fields, ignored_fields)
    fields = sorted(set(old_desired or {}) | set(live) | set(new_desired))
    changes: list[JsonObject] = []
    for field_path in fields:
        old_value = _value_or_missing(old_desired, field_path)
        live_value = _value_or_missing(live, field_path)
        new_value = _value_or_missing(new_desired, field_path)
        classification = classify_field_change(old_value, live_value, new_value)
        if classification == "no_change":
            continue
        changes.append(
            {
                "field_path": field_path,
                "classification": classification,
                "old_desired": old_value,
                "live": live_value,
                "new_desired": new_value,
                "before": live_value,
                "after": new_value,
            }
        )
    return changes


def apply_field_policy(
    fields: Mapping[str, Any] | None,
    managed_fields: Iterable[str] | None = None,
    ignored_fields: Iterable[str] | None = None,
) -> JsonObject | None:
    if fields is None:
        return None
    managed = set(managed_fields) if managed_fields is not None else None
    ignored = set(ignored_fields or ())
    return {
        path: value
        for path, value in fields.items()
        if (managed is None or path in managed) and path not in ignored
    }


def build_adoption_required_changes(
    *,
    live: Mapping[str, Any],
    new_desired: Mapping[str, Any],
    unknown_fields: Iterable[str],
) -> list[JsonObject]:
    changes: list[JsonObject] = []
    for field_path in sorted(set(unknown_fields)):
        live_value = _value_or_missing(live, field_path)
        new_value = _value_or_missing(new_desired, field_path)
        if live_value == MISSING and new_value == MISSING:
            continue
        changes.append(
            {
                "field_path": field_path,
                "classification": "adoption_required",
                "old_desired": MISSING,
                "live": live_value,
                "new_desired": new_value,
                "before": live_value,
                "after": new_value,
            }
        )
    return changes


def classify_field_change(old_desired: Any, live: Any, new_desired: Any) -> str:
    if old_desired == MISSING:
        return (
            "already_converged"
            if _declared_value_matches_live(new_desired, live)
            else "adoption_required"
        )
    old_matches_live = _declared_value_matches_live(old_desired, live)
    new_matches_live = _declared_value_matches_live(new_desired, live)
    desired_unchanged = old_desired == new_desired
    if old_matches_live and new_matches_live and desired_unchanged:
        return "no_change"
    if new_matches_live:
        return "already_converged"
    if old_matches_live:
        return "intended_change"
    if desired_unchanged:
        return "drift"
    return "conflict_or_manual_change"


def _declared_value_matches_live(declared: Any, live: Any) -> bool:
    """Return whether the API-observed value satisfies the declared value.

    Kubernetes adds defaults to nested objects after admission.  Those extra
    mapping keys are not Git drift because the repository never declared them.
    Declared list membership remains exact, however, so an extra port,
    container, or environment entry is still surfaced for review.
    """

    if isinstance(declared, Mapping):
        if not isinstance(live, Mapping):
            return False
        return all(
            key in live and _declared_value_matches_live(value, live[key])
            for key, value in declared.items()
        )
    if isinstance(declared, list):
        if not isinstance(live, list) or len(declared) != len(live):
            return False
        return all(
            _declared_value_matches_live(declared_item, live_item)
            for declared_item, live_item in zip(declared, live, strict=True)
        )
    return declared == live


def summarize_status(changes: list[JsonObject]) -> str:
    classes = {str(change["classification"]) for change in changes}
    if "conflict_or_manual_change" in classes:
        return "review_required"
    if "adoption_required" in classes:
        return "adoption_required"
    if "intended_change" in classes:
        return "intended_change"
    if "drift" in classes:
        return "drift"
    if "already_converged" in classes:
        return "already_converged"
    return "no_change"


def build_diff_basis(
    *,
    old_desired: ManagedFieldSnapshot | None,
    live: ManagedFieldSnapshot,
    new_desired: ManagedFieldSnapshot,
    declared_fields: Iterable[str] | None = None,
    policy_managed_fields: Iterable[str] | None = None,
    ignored_fields: Iterable[str] | None = None,
    unknown_fields: Iterable[str] | None = None,
    policy_source: str = "unset",
) -> JsonObject:
    return {
        "comparison": MANAGED_FIELD_SOURCE,
        "old_desired_source": old_desired.source if old_desired else "missing",
        "live_source": live.source,
        "new_desired_source": new_desired.source,
        "observed_fields": sorted(set(live.fields) | set(new_desired.fields)),
        "declared_fields": sorted(set(declared_fields or ())),
        "policy_managed_fields": sorted(set(policy_managed_fields or ())),
        "ignored_fields": sorted(set(ignored_fields or ())),
        "unknown_fields": sorted(set(unknown_fields or ())),
        "policy_source": policy_source,
    }


def _deployment_fields(obj: Mapping[str, Any]) -> JsonObject:
    spec = _mapping(obj.get("spec"))
    fields: JsonObject = {}
    if "replicas" in spec:
        fields["spec.replicas"] = spec["replicas"]

    pod_spec = _mapping(_mapping(_mapping(spec.get("template")).get("spec")))
    containers = pod_spec.get("containers", [])
    if isinstance(containers, list):
        for item in containers:
            container = _mapping(item)
            name = str(container.get("name", "unnamed"))
            prefix = f"spec.template.spec.containers[name={name}]"
            if "image" in container:
                fields[f"{prefix}.image"] = container["image"]
            if "env" in container:
                fields[f"{prefix}.env"] = container["env"]
            if "envFrom" in container:
                fields[f"{prefix}.envFrom"] = container["envFrom"]
            if "resources" in container:
                fields[f"{prefix}.resources"] = container["resources"]
            for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
                if probe in container:
                    fields[f"{prefix}.{probe}"] = container[probe]
    volumes = pod_spec.get("volumes", [])
    if isinstance(volumes, list):
        for item in volumes:
            volume = _mapping(item)
            name = str(volume.get("name", "unnamed"))
            prefix = f"spec.template.spec.volumes[name={name}]"
            if "configMap" in volume:
                fields[f"{prefix}.configMap"] = volume["configMap"]
            if "secret" in volume:
                fields[f"{prefix}.secret"] = volume["secret"]
    return fields


def _service_fields(obj: Mapping[str, Any]) -> JsonObject:
    spec = _mapping(obj.get("spec"))
    fields: JsonObject = {}
    for key in ("type", "selector", "ports"):
        if key in spec:
            fields[f"spec.{key}"] = spec[key]
    return fields


def _configmap_fields(obj: Mapping[str, Any]) -> JsonObject:
    fields: JsonObject = {}
    for key in ("data", "binaryData"):
        if key in obj:
            fields[key] = obj[key]
    return fields


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _value_or_missing(values: Mapping[str, Any] | None, field_path: str) -> Any:
    if values is None or field_path not in values:
        return MISSING
    return values[field_path]
