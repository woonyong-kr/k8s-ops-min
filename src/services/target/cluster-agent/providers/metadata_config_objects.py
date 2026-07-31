from __future__ import annotations

from packages.contracts.event_bus.interfaces import JsonObject
from providers.kubernetes_utils import (
    K8S_KIND_CONFIG_MAP,
    K8S_KIND_SECRET,
    K8S_RESOURCE_CONFIG_MAPS,
    K8S_RESOURCE_SECRETS,
    compact_dict,
    list_items,
    metadata,
    object_or_empty,
    safe_metadata_labels,
    spec,
)
from providers.metadata_config_refs import (
    env_from_refs,
    env_refs,
    volume_mount_refs,
    volume_reference_map,
)

CONFIG_OBJECT_NOT_FOUND = "not_found"
CONFIG_OBJECT_FORBIDDEN = "forbidden"
CONFIG_OBJECT_OK = "ok"
CONFIG_REF_SOURCE_ENV = "env"
CONFIG_REF_SOURCE_ENV_FROM = "env_from"
CONFIG_REF_SOURCE_VOLUME = "volume"
CONFIG_REF_SOURCE_VOLUME_MOUNT = "volume_mount"
MAX_CONFIG_OBJECT_LABELS = 12
MAX_CONFIG_OBJECT_LABEL_LENGTH = 120


def referenced_config_object_refs(
    deployment: JsonObject,
    namespace: str,
) -> list[JsonObject]:
    """Return ConfigMap and Secret objects used by one Deployment."""
    template = object_or_empty(spec(deployment).get("template"))
    template_spec = spec(template)
    volume_refs = volume_reference_map(template_spec)
    refs_by_key: dict[tuple[str, str, str], JsonObject] = {}

    for volume_ref in volume_refs.values():
        add_ref_from_summary(
            refs_by_key,
            namespace,
            volume_ref,
            {
                "source": CONFIG_REF_SOURCE_VOLUME,
                "volume_name": volume_ref.get("volume_name"),
                "optional": volume_ref.get("optional"),
            },
        )
        add_volume_key_refs(refs_by_key, namespace, volume_ref)

    for container in list_items(template_spec.get("containers")):
        container_name = container.get("name")
        for env_ref in env_refs(container):
            add_ref_from_summary(
                refs_by_key,
                namespace,
                env_ref,
                {
                    "container_name": container_name,
                    "source": CONFIG_REF_SOURCE_ENV,
                    "env_name": env_ref.get("env_name"),
                    "key": env_ref.get("key"),
                    "optional": env_ref.get("optional"),
                },
                referenced_key=env_ref.get("key"),
                key_source=CONFIG_REF_SOURCE_ENV,
            )
        for env_from_ref in env_from_refs(container):
            add_ref_from_summary(
                refs_by_key,
                namespace,
                env_from_ref,
                {
                    "container_name": container_name,
                    "source": CONFIG_REF_SOURCE_ENV_FROM,
                    "prefix": env_from_ref.get("prefix"),
                    "optional": env_from_ref.get("optional"),
                },
            )
        for mount_ref in volume_mount_refs(container, volume_refs):
            add_ref_from_summary(
                refs_by_key,
                namespace,
                mount_ref,
                {
                    "container_name": container_name,
                    "source": CONFIG_REF_SOURCE_VOLUME_MOUNT,
                    "volume_name": mount_ref.get("volume_name"),
                    "mount_path": mount_ref.get("mount_path"),
                    "read_only": mount_ref.get("read_only"),
                    "optional": mount_ref.get("optional"),
                },
            )

    return sorted(
        refs_by_key.values(),
        key=lambda item: (
            str(item.get("kind") or ""),
            str(item.get("namespace") or ""),
            str(item.get("name") or ""),
        ),
    )


def config_object_resource(kind: str) -> str:
    """Return the Kubernetes resource name for a config object kind."""
    if kind == K8S_KIND_CONFIG_MAP:
        return K8S_RESOURCE_CONFIG_MAPS
    if kind == K8S_KIND_SECRET:
        return K8S_RESOURCE_SECRETS
    raise ValueError(f"unsupported config object kind: {kind}")


def referenced_config_object_summary(
    reference: JsonObject,
    payload: JsonObject,
    access: str,
) -> JsonObject:
    """Build a safe referenced ConfigMap or Secret summary."""
    summary: JsonObject = {
        "kind": reference.get("kind"),
        "namespace": reference.get("namespace"),
        "name": reference.get("name"),
        "exists": access == CONFIG_OBJECT_OK,
        "access": access,
        "referenced_by": reference.get("referenced_by", []),
    }
    if access == CONFIG_OBJECT_FORBIDDEN:
        summary["exists"] = None
    if access == CONFIG_OBJECT_OK:
        meta = metadata(payload)
        summary["created_at"] = meta.get("creationTimestamp")
        summary["labels"] = safe_metadata_labels(
            meta.get("labels"),
            limit=MAX_CONFIG_OBJECT_LABELS,
            max_value_length=MAX_CONFIG_OBJECT_LABEL_LENGTH,
        )
        summary["referenced_key_checks"] = referenced_key_checks(reference, payload)
    return {
        key: value
        for key, value in summary.items()
        if key == "exists" or value not in (None, "", [], {})
    }


def referenced_key_checks(reference: JsonObject, payload: JsonObject) -> list[JsonObject]:
    """Return whether explicitly referenced keys exist."""
    available_keys = config_object_data_keys(payload)
    checks_by_key: dict[str, JsonObject] = {}
    for key_ref in list_items(reference.get("referenced_keys")):
        key = key_ref.get("key")
        if not isinstance(key, str) or not key:
            continue
        check = checks_by_key.setdefault(
            key,
            {
                "key": key,
                "exists": key in available_keys,
                "sources": [],
            },
        )
        source = key_ref.get("source")
        if isinstance(source, str) and source and source not in check["sources"]:
            check["sources"].append(source)
            check["sources"].sort()
    return sorted(checks_by_key.values(), key=lambda item: str(item.get("key") or ""))


def config_object_data_keys(payload: JsonObject) -> set[str]:
    """Return data key names without reading their values."""
    data = object_or_empty(payload.get("data"))
    binary_data = object_or_empty(payload.get("binaryData"))
    return {str(key) for key in data} | {str(key) for key in binary_data}


def add_ref_from_summary(
    refs_by_key: dict[tuple[str, str, str], JsonObject],
    namespace: str,
    summary: JsonObject,
    referenced_by: JsonObject,
    *,
    referenced_key: object = None,
    key_source: object = None,
) -> None:
    """Add one summarized ConfigMap or Secret ref to a grouped map."""
    kind_name = config_ref_kind_and_name(summary)
    if not kind_name:
        return
    kind, name = kind_name
    add_grouped_ref(
        refs_by_key,
        kind,
        namespace,
        name,
        referenced_by,
        referenced_key=referenced_key,
        key_source=key_source,
    )


def add_volume_key_refs(
    refs_by_key: dict[tuple[str, str, str], JsonObject],
    namespace: str,
    volume_ref: JsonObject,
) -> None:
    """Add explicit key refs from one ConfigMap or Secret volume."""
    kind_name = config_ref_kind_and_name(volume_ref)
    if not kind_name:
        return
    kind, name = kind_name
    for item in list_items(volume_ref.get("items")):
        add_grouped_key(
            refs_by_key,
            kind,
            namespace,
            name,
            item.get("key"),
            CONFIG_REF_SOURCE_VOLUME,
        )


def config_ref_kind_and_name(reference: JsonObject) -> tuple[str, str] | None:
    """Return the Kubernetes kind and name for one config reference."""
    config_map_name = reference.get("config_map_name")
    if isinstance(config_map_name, str) and config_map_name:
        return K8S_KIND_CONFIG_MAP, config_map_name
    secret_name = reference.get("secret_name")
    if isinstance(secret_name, str) and secret_name:
        return K8S_KIND_SECRET, secret_name
    return None


def add_grouped_ref(
    refs_by_key: dict[tuple[str, str, str], JsonObject],
    kind: str,
    namespace: str,
    name: str,
    referenced_by: JsonObject,
    *,
    referenced_key: object = None,
    key_source: object = None,
) -> None:
    """Add one config object reference to a grouped map."""
    existing = config_ref_bucket(refs_by_key, kind, namespace, name)
    compact_ref = compact_dict(referenced_by)
    if compact_ref and compact_ref not in existing["referenced_by"]:
        existing["referenced_by"].append(compact_ref)
        existing["referenced_by"].sort(key=referenced_by_sort_key)
    add_grouped_key(
        refs_by_key,
        kind,
        namespace,
        name,
        referenced_key,
        key_source,
    )


def add_grouped_key(
    refs_by_key: dict[tuple[str, str, str], JsonObject],
    kind: str,
    namespace: str,
    name: str,
    referenced_key: object,
    key_source: object,
) -> None:
    """Add one explicit config key reference to a grouped map."""
    if not isinstance(referenced_key, str) or not referenced_key:
        return
    existing = config_ref_bucket(refs_by_key, kind, namespace, name)
    key_ref = compact_dict({"key": referenced_key, "source": key_source})
    if key_ref and key_ref not in existing["referenced_keys"]:
        existing["referenced_keys"].append(key_ref)
        existing["referenced_keys"].sort(
            key=lambda item: (
                str(item.get("key") or ""),
                str(item.get("source") or ""),
            )
        )


def config_ref_bucket(
    refs_by_key: dict[tuple[str, str, str], JsonObject],
    kind: str,
    namespace: str,
    name: str,
) -> JsonObject:
    """Return the grouped reference bucket for one config object."""
    return refs_by_key.setdefault(
        (kind, namespace, name),
        {
            "kind": kind,
            "namespace": namespace,
            "name": name,
            "referenced_by": [],
            "referenced_keys": [],
        },
    )


def referenced_by_sort_key(reference: JsonObject) -> tuple[str, str, str, str]:
    """Return a stable sort key for reference locations."""
    return (
        str(reference.get("source") or ""),
        str(reference.get("container_name") or ""),
        str(reference.get("env_name") or ""),
        str(reference.get("volume_name") or ""),
    )
