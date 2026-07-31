"""Safe ConfigMap/Secret reference projection from persisted workload inventory."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from domains.inventory.coverage import COLLECTION_COVERAGE_SUMMARY_KEY, WORKLOAD_COLLECTION
from domains.inventory.resource_types import WORKLOAD_RESOURCE_TYPE
from domains.inventory.snapshot_evidence import snapshot_source_summary
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway.responses import (
    ConfigReferenceCoverage,
    ConfigReferenceItem,
    ConfigReferenceListResponse,
    ConfigReferenceUsage,
    ConfigReferenceWorkload,
)

JsonObject = dict[str, Any]

CONFIG_MAP_KIND = "ConfigMap"
DEPLOYMENT_KIND = "Deployment"
SECRET_KIND = "Secret"
CONFIG_REFERENCE_DEFAULT_WORKLOAD_LIMIT = gateway_limits.INVENTORY_RESOURCE_DEFAULT_LIMIT
CONFIG_REFERENCE_MAX_WORKLOAD_LIMIT = gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT
CONFIG_REFERENCE_MAX_ITEMS = gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT
CONFIG_REFERENCE_MAX_USAGES = gateway_limits.INVENTORY_RESOURCE_MAX_LIMIT
CONFIG_REFERENCE_MAX_REASON_CODES = gateway_limits.CONFIG_REFERENCE_REASON_CODE_MAX_COUNT
CONFIG_REFERENCE_TEXT_MAX_LENGTH = gateway_limits.KUBERNETES_NAME_MAX_LENGTH
CONFIG_REFERENCE_PATH_MAX_LENGTH = gateway_limits.FILTER_VALUE_LIST_MAX_LENGTH
CONFIG_REFERENCE_REASON_MAX_LENGTH = 160
CONFIG_REFERENCE_TIMESTAMP_MAX_LENGTH = 80
CONFIG_REFERENCE_REASONS_TRUNCATED = "config_reference_reason_codes_truncated"


@dataclass
class ConfigReferenceProjection:
    items: list[ConfigReferenceItem]
    limited: bool


@dataclass
class ConfigReferenceProjectionState:
    items_by_key: dict[tuple[str, str, str], JsonObject] = field(default_factory=dict)
    usage_keys: set[tuple[object, ...]] = field(default_factory=set)
    limited: bool = False


def config_reference_list_response(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespace: str | None,
    workload_limit: int = CONFIG_REFERENCE_DEFAULT_WORKLOAD_LIMIT,
) -> ConfigReferenceListResponse:
    """Project only Deployment -> ConfigMap/Secret reference identities."""

    namespace, namespace_error = normalize_namespace(namespace)
    if namespace_error is not None:
        return ConfigReferenceListResponse(
            cluster_id=cluster_id,
            namespace=namespace,
            items=[],
            coverage=ConfigReferenceCoverage(
                availability="unavailable",
                workload_count=0,
                projected_reference_count=0,
                reason_codes=(namespace_error,),
            ),
        )

    effective_limit = bounded_workload_limit(workload_limit)
    read_limit = min(effective_limit + 1, CONFIG_REFERENCE_MAX_WORKLOAD_LIMIT)
    latest_snapshot = latest_inventory_snapshot(db, workspace_id, cluster_id)
    if latest_snapshot is None:
        return ConfigReferenceListResponse(
            cluster_id=cluster_id,
            namespace=namespace,
            items=[],
            coverage=ConfigReferenceCoverage(
                availability="unavailable",
                workload_count=0,
                projected_reference_count=0,
                reason_codes=("inventory_snapshot_unavailable",),
            ),
        )

    deployments = list_deployments(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        limit=read_limit,
    )
    if deployments is None:
        return ConfigReferenceListResponse(
            cluster_id=cluster_id,
            namespace=namespace,
            items=[],
            coverage=ConfigReferenceCoverage(
                availability="unavailable",
                snapshot_id=bounded_cluster_text_or_none(latest_snapshot.get("snapshot_id")),
                observed_at=bounded_timestamp_or_none(
                    latest_snapshot.get("collected_at") or latest_snapshot.get("created_at")
                ),
                workload_count=0,
                projected_reference_count=0,
                reason_codes=("inventory_resource_repository_unavailable",),
            ),
        )

    projection_deployments = deployments[:effective_limit]
    projection = project_config_references(projection_deployments)
    reason_codes = coverage_reason_codes(
        latest_snapshot,
        namespace=namespace,
        deployment_projection_limited=len(deployments) > effective_limit
        or (
            effective_limit == CONFIG_REFERENCE_MAX_WORKLOAD_LIMIT
            and len(deployments) >= CONFIG_REFERENCE_MAX_WORKLOAD_LIMIT
        ),
        reference_projection_limited=projection.limited,
    )
    return ConfigReferenceListResponse(
        cluster_id=cluster_id,
        namespace=namespace,
        items=projection.items,
        coverage=ConfigReferenceCoverage(
            availability="available" if not reason_codes else "partial",
            snapshot_id=bounded_cluster_text_or_none(latest_snapshot.get("snapshot_id")),
            observed_at=bounded_timestamp_or_none(
                latest_snapshot.get("collected_at") or latest_snapshot.get("created_at")
            ),
            workload_count=len(projection_deployments),
            projected_reference_count=len(projection.items),
            reason_codes=tuple(bounded_reason_codes(reason_codes)),
        ),
    )


def latest_inventory_snapshot(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> Mapping[str, object] | None:
    reader = getattr(db, "latest_inventory_snapshot", None)
    if not callable(reader):
        return None
    value = reader(workspace_id, cluster_id)
    return value if isinstance(value, Mapping) else None


def list_deployments(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespace: str | None,
    limit: int,
) -> list[Mapping[str, object]] | None:
    reader = getattr(db, "list_inventory_resources_by_kind", None)
    if not callable(reader):
        return None
    rows = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=WORKLOAD_RESOURCE_TYPE,
        kind=DEPLOYMENT_KIND,
        namespace=namespace,
        include_deleted=False,
        limit=limit,
    )
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return None
    return [row for row in rows if isinstance(row, Mapping)]


def project_config_references(
    deployments: Sequence[Mapping[str, object]],
) -> ConfigReferenceProjection:
    state = ConfigReferenceProjectionState()
    for deployment in deployments:
        project_deployment_config_references(deployment, state)
        if state.limited:
            break

    return ConfigReferenceProjection(
        items=[
            ConfigReferenceItem(
                kind=str(item["kind"]),
                namespace=str(item["namespace"]),
                name=str(item["name"]),
                referenced_by=[
                    ConfigReferenceUsage(**usage) for usage in item.get("referenced_by", [])
                ],
            )
            for item in sorted(
                state.items_by_key.values(),
                key=lambda value: (
                    str(value.get("kind") or ""),
                    str(value.get("namespace") or ""),
                    str(value.get("name") or ""),
                ),
            )
        ],
        limited=state.limited,
    )


def project_deployment_config_references(
    deployment: Mapping[str, object],
    state: ConfigReferenceProjectionState,
) -> None:
    raw = mapping(deployment.get("raw"))
    meta = mapping(raw.get("metadata"))
    namespace = bounded_text_or_none(deployment.get("namespace")) or bounded_text_or_none(
        meta.get("namespace")
    )
    name = bounded_text_or_none(deployment.get("name")) or bounded_text_or_none(meta.get("name"))
    if namespace is None or name is None:
        return

    workload = ConfigReferenceWorkload(
        kind=DEPLOYMENT_KIND,
        namespace=namespace,
        name=name,
        uid=bounded_text_or_none(deployment.get("uid")) or bounded_text_or_none(meta.get("uid")),
    )
    template_spec = deployment_template_spec(deployment, raw)
    volume_refs = volume_reference_map(template_spec)
    for volume_ref in volume_refs.values():
        add_config_reference(
            state,
            kind=str(volume_ref["kind"]),
            namespace=namespace,
            name=str(volume_ref["name"]),
            usage={
                "workload": workload,
                "source": "volume",
                "volume_name": volume_ref.get("volume_name"),
                "optional": volume_ref.get("optional"),
            },
        )
        if state.limited:
            return

    for container in container_items(template_spec):
        container_name = bounded_text_or_none(container.get("name"))
        for ref in env_value_refs(container):
            add_config_reference(
                state,
                kind=str(ref["kind"]),
                namespace=namespace,
                name=str(ref["name"]),
                usage={
                    "workload": workload,
                    "source": "env",
                    "container_name": container_name,
                    "env_name": ref.get("env_name"),
                    "key": ref.get("key"),
                    "optional": ref.get("optional"),
                },
            )
            if state.limited:
                return
        for ref in env_from_refs(container):
            add_config_reference(
                state,
                kind=str(ref["kind"]),
                namespace=namespace,
                name=str(ref["name"]),
                usage={
                    "workload": workload,
                    "source": "env_from",
                    "container_name": container_name,
                    "prefix": ref.get("prefix"),
                    "optional": ref.get("optional"),
                },
            )
            if state.limited:
                return
        for mount in list_items(container.get("volumeMounts")):
            volume_name = bounded_text_or_none(mount.get("name"))
            if volume_name is None or volume_name not in volume_refs:
                continue
            volume_ref = volume_refs[volume_name]
            add_config_reference(
                state,
                kind=str(volume_ref["kind"]),
                namespace=namespace,
                name=str(volume_ref["name"]),
                usage={
                    "workload": workload,
                    "source": "volume_mount",
                    "container_name": container_name,
                    "volume_name": volume_name,
                    "mount_path": bounded_path_or_none(mount.get("mountPath")),
                    "read_only": bool_or_none(mount.get("readOnly")),
                    "optional": volume_ref.get("optional"),
                },
            )
            if state.limited:
                return


def volume_reference_map(template_spec: Mapping[str, object]) -> dict[str, JsonObject]:
    refs: dict[str, JsonObject] = {}
    for volume in list_items(template_spec.get("volumes")):
        volume_name = bounded_text_or_none(volume.get("name"))
        if volume_name is None:
            continue
        config_map = mapping(volume.get("configMap"))
        if config_map:
            name = bounded_text_or_none(config_map.get("name"))
            if name is not None:
                refs[volume_name] = {
                    "kind": CONFIG_MAP_KIND,
                    "name": name,
                    "volume_name": volume_name,
                    "optional": bool_or_none(config_map.get("optional")),
                }
            continue
        secret = mapping(volume.get("secret"))
        if secret:
            name = bounded_text_or_none(secret.get("secretName"))
            if name is not None:
                refs[volume_name] = {
                    "kind": SECRET_KIND,
                    "name": name,
                    "volume_name": volume_name,
                    "optional": bool_or_none(secret.get("optional")),
                }
    return refs


def env_value_refs(container: Mapping[str, object]) -> list[JsonObject]:
    refs: list[JsonObject] = []
    for env in list_items(container.get("env")):
        value_from = mapping(env.get("valueFrom"))
        config_map = mapping(value_from.get("configMapKeyRef"))
        if config_map:
            name = bounded_text_or_none(config_map.get("name"))
            if name is not None:
                refs.append(
                    {
                        "kind": CONFIG_MAP_KIND,
                        "name": name,
                        "env_name": bounded_text_or_none(env.get("name")),
                        "key": bounded_text_or_none(config_map.get("key")),
                        "optional": bool_or_none(config_map.get("optional")),
                    }
                )

        secret = mapping(value_from.get("secretKeyRef"))
        if secret:
            name = bounded_text_or_none(secret.get("name"))
            if name is not None:
                refs.append(
                    {
                        "kind": SECRET_KIND,
                        "name": name,
                        "env_name": bounded_text_or_none(env.get("name")),
                        "key": bounded_text_or_none(secret.get("key")),
                        "optional": bool_or_none(secret.get("optional")),
                    }
                )
    return refs


def env_from_refs(container: Mapping[str, object]) -> list[JsonObject]:
    refs: list[JsonObject] = []
    for env_from in list_items(container.get("envFrom")):
        config_map = mapping(env_from.get("configMapRef"))
        if config_map:
            name = bounded_text_or_none(config_map.get("name"))
            if name is not None:
                refs.append(
                    {
                        "kind": CONFIG_MAP_KIND,
                        "name": name,
                        "prefix": bounded_text_or_none(env_from.get("prefix")),
                        "optional": bool_or_none(config_map.get("optional")),
                    }
                )

        secret = mapping(env_from.get("secretRef"))
        if secret:
            name = bounded_text_or_none(secret.get("name"))
            if name is not None:
                refs.append(
                    {
                        "kind": SECRET_KIND,
                        "name": name,
                        "prefix": bounded_text_or_none(env_from.get("prefix")),
                        "optional": bool_or_none(secret.get("optional")),
                    }
                )
    return refs


def deployment_template_spec(
    deployment: Mapping[str, object],
    raw: Mapping[str, object],
) -> Mapping[str, object]:
    """Support both persisted workload summaries and full Kubernetes objects."""

    summary = mapping(deployment.get("summary"))
    for source in (raw, summary):
        pod_template_spec = mapping(mapping(source.get("pod_template")).get("spec"))
        if pod_template_spec:
            return pod_template_spec
        kubernetes_template_spec = mapping(
            mapping(mapping(source.get("spec")).get("template")).get("spec")
        )
        if kubernetes_template_spec:
            return kubernetes_template_spec
    return {}


def container_items(template_spec: Mapping[str, object]) -> list[Mapping[str, object]]:
    containers: list[Mapping[str, object]] = []
    containers.extend(list_items(template_spec.get("containers")))
    containers.extend(list_items(template_spec.get("initContainers")))
    return containers[: CONFIG_REFERENCE_MAX_USAGES + 1]


def add_config_reference(
    state: ConfigReferenceProjectionState,
    *,
    kind: str,
    namespace: str,
    name: str,
    usage: Mapping[str, object],
) -> None:
    if kind not in (CONFIG_MAP_KIND, SECRET_KIND) or not namespace or not name:
        return
    key = (kind, namespace, name)
    normalized_usage = compact_usage(usage)
    dedupe_key = usage_dedupe_key(kind, namespace, name, normalized_usage)
    if dedupe_key in state.usage_keys:
        return
    if key not in state.items_by_key and len(state.items_by_key) >= CONFIG_REFERENCE_MAX_ITEMS:
        state.limited = True
        return
    if len(state.usage_keys) >= CONFIG_REFERENCE_MAX_USAGES:
        state.limited = True
        return
    item = state.items_by_key.setdefault(
        key,
        {"kind": kind, "namespace": namespace, "name": name, "referenced_by": []},
    )
    state.usage_keys.add(dedupe_key)
    item["referenced_by"].append(normalized_usage)
    item["referenced_by"].sort(key=usage_sort_key)


def compact_usage(usage: Mapping[str, object]) -> JsonObject:
    return {
        key: value
        for key, value in usage.items()
        if value is not None and value not in ("", [], {})
    }


def usage_dedupe_key(
    kind: str,
    namespace: str,
    name: str,
    usage: Mapping[str, object],
) -> tuple[object, ...]:
    workload = usage.get("workload")
    workload_key: tuple[object, ...] = ()
    if isinstance(workload, ConfigReferenceWorkload):
        workload_key = (workload.kind, workload.namespace, workload.name, workload.uid)
    elif isinstance(workload, Mapping):
        workload_key = (
            workload.get("kind"),
            workload.get("namespace"),
            workload.get("name"),
            workload.get("uid"),
        )
    return (
        kind,
        namespace,
        name,
        workload_key,
        usage.get("source"),
        usage.get("container_name"),
        usage.get("env_name"),
        usage.get("key"),
        usage.get("prefix"),
        usage.get("volume_name"),
        usage.get("mount_path"),
    )


def usage_sort_key(usage: Mapping[str, object]) -> tuple[str, ...]:
    workload = usage.get("workload")
    workload_namespace = ""
    workload_name = ""
    if isinstance(workload, ConfigReferenceWorkload):
        workload_namespace = workload.namespace
        workload_name = workload.name
    elif isinstance(workload, Mapping):
        workload_namespace = str(workload.get("namespace") or "")
        workload_name = str(workload.get("name") or "")
    return (
        workload_namespace,
        workload_name,
        str(usage.get("container_name") or ""),
        str(usage.get("source") or ""),
        str(usage.get("env_name") or ""),
        str(usage.get("volume_name") or ""),
        str(usage.get("mount_path") or ""),
    )


def coverage_reason_codes(
    latest_snapshot: Mapping[str, object],
    *,
    namespace: str | None,
    deployment_projection_limited: bool,
    reference_projection_limited: bool,
) -> list[str]:
    reasons: list[str] = []
    source_summary = snapshot_source_summary(latest_snapshot) or {}
    workload_reasons = workload_coverage_reason_codes(source_summary, namespace)
    if workload_reasons is not None:
        reasons.extend(f"workload_{reason}" for reason in workload_reasons)
    else:
        collection_limits = mapping(source_summary.get("collection_limits"))
        if collection_limits.get("truncated") is True:
            reasons.append("source_resources_truncated")
        elif source_summary.get("resources_complete") is not True:
            reasons.append("source_resources_incomplete")
    if deployment_projection_limited:
        reasons.append("deployment_projection_limit_reached")
    if reference_projection_limited:
        reasons.append("config_reference_projection_limit_reached")
    return unique(reasons)


def bounded_reason_codes(values: Sequence[str]) -> list[str]:
    reasons = unique(values)
    if len(reasons) <= CONFIG_REFERENCE_MAX_REASON_CODES:
        return reasons
    return [
        *reasons[: CONFIG_REFERENCE_MAX_REASON_CODES - 1],
        CONFIG_REFERENCE_REASONS_TRUNCATED,
    ]


def workload_coverage_reason_codes(
    source_summary: Mapping[str, object],
    namespace: str | None,
) -> list[str] | None:
    coverage = source_summary.get(COLLECTION_COVERAGE_SUMMARY_KEY)
    if not isinstance(coverage, Sequence) or isinstance(coverage, (str, bytes, bytearray)):
        return None
    candidates = [
        item
        for item in coverage
        if isinstance(item, Mapping)
        and item.get("collection") == WORKLOAD_COLLECTION
        and (
            namespace is None
            or item.get("namespace") == namespace
            or item.get("scope") == "cluster"
        )
    ]
    if not candidates:
        if namespace is not None:
            return ["collection_not_observed"]
        return None
    exact = [
        item
        for item in candidates
        if namespace is not None and item.get("namespace") == namespace
    ]
    selected = exact or candidates
    if all(item.get("complete") is True for item in selected):
        return []
    reasons = [
        reason
        for item in selected
        for reason in text_list(item.get("reason_codes"))
    ]
    return reasons or ["collection_incomplete"]


def bounded_workload_limit(limit: int) -> int:
    return max(1, min(int(limit), CONFIG_REFERENCE_MAX_WORKLOAD_LIMIT))


def bounded_text_or_none(value: object) -> str | None:
    return text_or_none(value, max_length=CONFIG_REFERENCE_TEXT_MAX_LENGTH)


def bounded_path_or_none(value: object) -> str | None:
    return text_or_none(value, max_length=CONFIG_REFERENCE_PATH_MAX_LENGTH)


def bounded_cluster_text_or_none(value: object) -> str | None:
    return text_or_none(value, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH)


def bounded_timestamp_or_none(value: object) -> str | None:
    return text_or_none(value, max_length=CONFIG_REFERENCE_TIMESTAMP_MAX_LENGTH)


def mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def list_items(
    value: object,
    *,
    limit: int = CONFIG_REFERENCE_MAX_USAGES + 1,
) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items: list[Mapping[str, object]] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(item)
            if len(items) >= limit:
                break
    return items


def text_or_none(value: object, *, max_length: int | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if max_length is not None and len(stripped) > max_length:
        return None
    return stripped or None


def normalize_namespace(value: str | None) -> tuple[str | None, str | None]:
    normalized = text_or_none(value)
    if normalized is None:
        return None, None
    if len(normalized) > CONFIG_REFERENCE_TEXT_MAX_LENGTH:
        return None, "invalid_namespace"
    return normalized, None


def bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def text_list(
    value: object,
    *,
    limit: int = CONFIG_REFERENCE_MAX_REASON_CODES + 1,
) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items: list[str] = []
    for item in value:
        text = text_or_none(item, max_length=CONFIG_REFERENCE_REASON_MAX_LENGTH)
        if text is None:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
