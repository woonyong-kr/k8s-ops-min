from __future__ import annotations

from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

K8S_KIND_CONFIG_MAP = "ConfigMap"
K8S_KIND_DEPLOYMENT = "Deployment"
K8S_KIND_REPLICA_SET = "ReplicaSet"
K8S_KIND_SECRET = "Secret"
K8S_DEPLOYMENT_REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
K8S_ENDPOINT_SLICE_SERVICE_NAME_LABEL = "kubernetes.io/service-name"
K8S_RESOURCE_CONFIG_MAPS = "configmaps"
K8S_RESOURCE_DEPLOYMENTS = "deployments"
K8S_RESOURCE_ENDPOINT_SLICES = "endpointslices"
K8S_RESOURCE_PODS = "pods"
K8S_RESOURCE_REPLICASETS = "replicasets"
K8S_RESOURCE_CONTROLLER_REVISIONS = "controllerrevisions"
K8S_RESOURCE_RESOURCE_QUOTAS = "resourcequotas"
K8S_RESOURCE_SECRETS = "secrets"
K8S_RESOURCE_SERVICES = "services"
SENSITIVE_METADATA_TOKENS = (
    "authorization",
    "credential",
    "password",
    "private",
    "secret",
    "token",
)
DEFAULT_SAFE_METADATA_LABEL_LIMIT = 12
DEFAULT_SAFE_METADATA_LABEL_VALUE_LENGTH = 120
PREFERRED_METADATA_LABEL_KEYS = (
    "opsia.dev/recovery-continuity",
    "app",
    "app.kubernetes.io/name",
    "app.kubernetes.io/instance",
    "app.kubernetes.io/component",
    "app.kubernetes.io/part-of",
    "app.kubernetes.io/version",
    "component",
    "service",
    "tier",
    "version",
    "release",
)


def items(payload: Any) -> list[JsonObject]:
    """Return list items from a Kubernetes list response."""
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def metadata(item: JsonObject) -> JsonObject:
    """Return object metadata, or an empty dict."""
    value = item.get("metadata", {})
    return value if isinstance(value, dict) else {}


def spec(item: JsonObject) -> JsonObject:
    """Return object spec, or an empty dict."""
    value = item.get("spec", {})
    return value if isinstance(value, dict) else {}


def status(item: JsonObject) -> JsonObject:
    """Return object status, or an empty dict."""
    value = item.get("status", {})
    return value if isinstance(value, dict) else {}


def list_items(value: Any) -> list[JsonObject]:
    """Return dict items from a list value."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def object_or_empty(value: Any) -> JsonObject:
    """Return a dict value, or an empty dict."""
    return value if isinstance(value, dict) else {}


def compact_dict(value: JsonObject) -> JsonObject:
    """Drop empty values while keeping false boolean values."""
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def safe_metadata_labels(
    value: Any,
    *,
    limit: int = DEFAULT_SAFE_METADATA_LABEL_LIMIT,
    max_value_length: int = DEFAULT_SAFE_METADATA_LABEL_VALUE_LENGTH,
) -> JsonObject:
    """Return bounded labels without sensitive key or value text."""
    labels = object_or_empty(value)
    safe: JsonObject = {}
    for key in ordered_metadata_label_keys(labels):
        item = labels[key]
        key_text = str(key)
        value_text = str(item)
        if not safe_metadata_text(key_text) or not safe_metadata_text(value_text):
            continue
        safe[key_text] = value_text[:max_value_length]
        if len(safe) >= limit:
            break
    return safe


def ordered_metadata_label_keys(labels: JsonObject) -> list[object]:
    """Keep common identity labels before stable sorted labels."""
    preferred = [key for key in PREFERRED_METADATA_LABEL_KEYS if key in labels]
    remaining = sorted(
        (key for key in labels if key not in PREFERRED_METADATA_LABEL_KEYS),
        key=str,
    )
    return [*preferred, *remaining]


def safe_metadata_text(value: str) -> bool:
    """Return whether metadata text is safe to expose."""
    lowered = value.casefold()
    return not any(token in lowered for token in SENSITIVE_METADATA_TOKENS)


def resource_identity_snapshot(resource: JsonObject) -> JsonObject:
    """Return a small namespace/name identity."""
    meta = metadata(resource)
    return compact_dict(
        {
            "namespace": meta.get("namespace"),
            "name": meta.get("name"),
        }
    )


def resource_identity_key(identity: JsonObject) -> tuple[str, str] | None:
    """Return a tuple key for a namespace/name identity."""
    namespace = identity.get("namespace")
    name = identity.get("name")
    if not namespace or not name:
        return None
    return (str(namespace), str(name))


def resource_sort_key(resource: JsonObject) -> tuple[str, str]:
    """Return a stable sort key for Kubernetes resources."""
    meta = metadata(resource)
    return (
        str(meta.get("namespace") or ""),
        str(meta.get("name") or ""),
    )
