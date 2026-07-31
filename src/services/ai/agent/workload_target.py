"""관측 workload를 GitOps가 관리하는 controller target으로 안전하게 해석한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from packages.contracts.event_bus.bodies import JsonObject

AUTHORITY_TARGET_KEY = "gitops_target"
ORIGINAL_TARGET_KEY = "original_target"
TARGET_RESOLUTION_KEY = "gitops_target_resolution"
WORKLOAD_SNAPSHOT_SOURCE = "metadata:current_workload_snapshots"
CONTROLLER_RESOLUTION_KINDS = frozenset({"pod", "replicaset"})


@dataclass(frozen=True)
class WorkloadTarget:
    namespace: str
    resource_kind: str
    resource_name: str
    original_namespace: str
    original_resource_kind: str
    original_resource_name: str
    resolution_source: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.resolution_source)

    def identity(self) -> JsonObject:
        return {
            "namespace": self.namespace,
            "resource_kind": self.resource_kind,
            "resource_name": self.resource_name,
        }

    def original_identity(self) -> JsonObject:
        return {
            "namespace": self.original_namespace,
            "resource_kind": self.original_resource_kind,
            "resource_name": self.original_resource_name,
        }

    def resolution_metadata(self) -> JsonObject:
        if not self.resolved:
            return {}
        return {
            AUTHORITY_TARGET_KEY: self.identity(),
            ORIGINAL_TARGET_KEY: self.original_identity(),
            TARGET_RESOLUTION_KEY: self.resolution_source,
        }


def resolve_workload_target(
    namespace: object,
    resource_kind: object,
    resource_name: object,
    metadata: Mapping[str, object] | None = None,
) -> WorkloadTarget:
    """Pod/ReplicaSet만 명시적 snapshot ownership으로 Deployment에 올린다.

    이름 prefix 추측은 사용하지 않는다. 같은 namespace에서 incident resource를
    소유한다고 명시한 Deployment snapshot이 정확히 하나일 때만 승격한다.
    """

    original = WorkloadTarget(
        namespace=normalized_text(namespace),
        resource_kind=normalized_text(resource_kind),
        resource_name=normalized_text(resource_name),
        original_namespace=normalized_text(namespace),
        original_resource_kind=normalized_text(resource_kind),
        original_resource_name=normalized_text(resource_name),
    )
    if (
        original.resource_kind.casefold() not in CONTROLLER_RESOLUTION_KINDS
        or not all((original.namespace, original.resource_kind, original.resource_name))
    ):
        return original

    candidates = [
        candidate
        for snapshot in workload_snapshots(metadata)
        if (
            candidate := deployment_owner_from_snapshot(
                snapshot,
                original.namespace,
                original.resource_kind,
                original.resource_name,
            )
        )
    ]
    if len(candidates) != 1:
        return original
    namespace_value, name_value = candidates[0]
    return WorkloadTarget(
        namespace=namespace_value,
        resource_kind="Deployment",
        resource_name=name_value,
        original_namespace=original.namespace,
        original_resource_kind=original.resource_kind,
        original_resource_name=original.resource_name,
        resolution_source=WORKLOAD_SNAPSHOT_SOURCE,
    )


def resolved_target_from_metadata(
    namespace: object,
    resource_kind: object,
    resource_name: object,
    *metadata_values: Mapping[str, object] | None,
) -> WorkloadTarget:
    """여러 내부 metadata 후보가 있으면 동일한 해석일 때만 채택한다."""

    original = resolve_workload_target(namespace, resource_kind, resource_name)
    resolved: list[WorkloadTarget] = []
    for metadata in metadata_values:
        declared = declared_resolved_target(original, metadata)
        candidate = declared or resolve_workload_target(
            original.namespace,
            original.resource_kind,
            original.resource_name,
            metadata,
        )
        if candidate.resolved:
            resolved.append(candidate)
    identities = {
        (item.namespace, item.resource_kind.casefold(), item.resource_name)
        for item in resolved
    }
    if len(identities) != 1:
        return original
    return resolved[0]


def declared_resolved_target(
    original: WorkloadTarget,
    metadata: Mapping[str, object] | None,
) -> WorkloadTarget | None:
    """내부 plan에 보존된 resolution lineage가 완전할 때만 재사용한다."""

    if not isinstance(metadata, Mapping):
        return None
    if normalized_text(metadata.get(TARGET_RESOLUTION_KEY)) != WORKLOAD_SNAPSHOT_SOURCE:
        return None
    declared_original = metadata.get(ORIGINAL_TARGET_KEY)
    declared_target = metadata.get(AUTHORITY_TARGET_KEY)
    if not isinstance(declared_original, Mapping) or not isinstance(declared_target, Mapping):
        return None
    if (
        normalized_text(declared_original.get("namespace")) != original.namespace
        or normalized_text(declared_original.get("resource_kind")).casefold()
        != original.resource_kind.casefold()
        or normalized_text(declared_original.get("resource_name")) != original.resource_name
    ):
        return None
    namespace = normalized_text(declared_target.get("namespace"))
    resource_kind = normalized_text(declared_target.get("resource_kind"))
    resource_name = normalized_text(declared_target.get("resource_name"))
    if (
        original.resource_kind.casefold() not in CONTROLLER_RESOLUTION_KINDS
        or namespace != original.namespace
        or resource_kind.casefold() != "deployment"
        or not resource_name
    ):
        return None
    return WorkloadTarget(
        namespace=namespace,
        resource_kind="Deployment",
        resource_name=resource_name,
        original_namespace=original.namespace,
        original_resource_kind=original.resource_kind,
        original_resource_name=original.resource_name,
        resolution_source=WORKLOAD_SNAPSHOT_SOURCE,
    )


def workload_snapshots(
    metadata: Mapping[str, object] | None,
) -> list[Mapping[str, object]]:
    if not isinstance(metadata, Mapping):
        return []
    raw = metadata.get("current_workload_snapshots")
    if not isinstance(raw, list):
        change_context = metadata.get("change_context")
        if isinstance(change_context, Mapping):
            raw = change_context.get("current_workload_snapshots")
    if not isinstance(raw, list):
        raw = metadata.get("items")
    if not isinstance(raw, list):
        raw = metadata.get("workloads")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def deployment_owner_from_snapshot(
    snapshot: Mapping[str, object],
    namespace: str,
    resource_kind: str,
    resource_name: str,
) -> tuple[str, str] | None:
    workload = snapshot.get("workload")
    if not isinstance(workload, Mapping):
        return None
    workload_kind = normalized_text(workload.get("kind"))
    workload_namespace = normalized_text(workload.get("namespace"))
    workload_name = normalized_text(workload.get("name"))
    if (
        workload_kind.casefold() != "deployment"
        or workload_namespace != namespace
        or not workload_name
    ):
        return None
    ownership_key = (
        "replicaset_revisions" if resource_kind.casefold() == "replicaset" else "pod_statuses"
    )
    owned = snapshot.get(ownership_key)
    if not isinstance(owned, list):
        return None
    matches = [
        item
        for item in owned
        if isinstance(item, Mapping) and normalized_text(item.get("name")) == resource_name
    ]
    if len(matches) != 1:
        return None
    return workload_namespace, workload_name


def normalized_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
