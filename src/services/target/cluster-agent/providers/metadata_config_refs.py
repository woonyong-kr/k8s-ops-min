from __future__ import annotations

from packages.contracts.event_bus.interfaces import JsonObject
from providers.kubernetes_utils import compact_dict, list_items, object_or_empty


def env_refs(container: JsonObject) -> list[JsonObject]:
    """Return ConfigMap and Secret refs used by env values."""
    refs: list[JsonObject] = []
    for env in list_items(container.get("env")):
        value_from = object_or_empty(env.get("valueFrom"))
        config_map_ref = object_or_empty(value_from.get("configMapKeyRef"))
        if config_map_ref:
            refs.append(
                compact_dict(
                    {
                        "env_name": env.get("name"),
                        "source": "config_map_key_ref",
                        "config_map_name": config_map_ref.get("name"),
                        "key": config_map_ref.get("key"),
                        "optional": config_map_ref.get("optional"),
                    }
                )
            )

        secret_ref = object_or_empty(value_from.get("secretKeyRef"))
        if secret_ref:
            refs.append(
                compact_dict(
                    {
                        "env_name": env.get("name"),
                        "source": "secret_key_ref",
                        "secret_name": secret_ref.get("name"),
                        "key": secret_ref.get("key"),
                        "optional": secret_ref.get("optional"),
                    }
                )
            )

    return refs


def env_from_refs(container: JsonObject) -> list[JsonObject]:
    """Return ConfigMap and Secret refs used by envFrom."""
    refs: list[JsonObject] = []
    for env_from in list_items(container.get("envFrom")):
        config_map_ref = object_or_empty(env_from.get("configMapRef"))
        if config_map_ref:
            refs.append(
                compact_dict(
                    {
                        "source": "config_map_ref",
                        "config_map_name": config_map_ref.get("name"),
                        "prefix": env_from.get("prefix"),
                        "optional": config_map_ref.get("optional"),
                    }
                )
            )

        secret_ref = object_or_empty(env_from.get("secretRef"))
        if secret_ref:
            refs.append(
                compact_dict(
                    {
                        "source": "secret_ref",
                        "secret_name": secret_ref.get("name"),
                        "prefix": env_from.get("prefix"),
                        "optional": secret_ref.get("optional"),
                    }
                )
            )

    return refs


def volume_reference_map(template_spec: JsonObject) -> dict[str, JsonObject]:
    """Return ConfigMap and Secret volume refs by volume name."""
    refs: dict[str, JsonObject] = {}
    for volume in list_items(template_spec.get("volumes")):
        volume_name = volume.get("name")
        if not isinstance(volume_name, str) or not volume_name:
            continue

        config_map = object_or_empty(volume.get("configMap"))
        if config_map:
            refs[volume_name] = compact_dict(
                {
                    "volume_name": volume_name,
                    "source": "config_map",
                    "config_map_name": config_map.get("name"),
                    "optional": config_map.get("optional"),
                    "items": volume_items(config_map),
                }
            )
            continue

        secret = object_or_empty(volume.get("secret"))
        if secret:
            refs[volume_name] = compact_dict(
                {
                    "volume_name": volume_name,
                    "source": "secret",
                    "secret_name": secret.get("secretName"),
                    "optional": secret.get("optional"),
                    "items": volume_items(secret),
                }
            )

    return refs


def volume_mount_refs(
    container: JsonObject,
    volume_refs: dict[str, JsonObject],
) -> list[JsonObject]:
    """Return ConfigMap and Secret refs mounted by one container."""
    refs: list[JsonObject] = []
    for mount in list_items(container.get("volumeMounts")):
        volume_name = mount.get("name")
        if not isinstance(volume_name, str):
            continue
        volume_ref = volume_refs.get(volume_name)
        if not volume_ref:
            continue
        refs.append(
            compact_dict(
                {
                    **volume_ref,
                    "mount_path": mount.get("mountPath"),
                    "read_only": mount.get("readOnly"),
                    "sub_path": mount.get("subPath"),
                }
            )
        )
    return refs


def volume_items(volume_source: JsonObject) -> list[JsonObject]:
    """Return item keys and paths without reading item values."""
    return [
        compact_dict(
            {
                "key": item.get("key"),
                "path": item.get("path"),
            }
        )
        for item in list_items(volume_source.get("items"))
    ]
