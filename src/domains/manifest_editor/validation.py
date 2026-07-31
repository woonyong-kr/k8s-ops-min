"""Fail-closed validation for browser-authored GitOps YAML edits."""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml
from yaml.tokens import AliasToken, AnchorToken

MAX_MANIFEST_BYTES = 1_048_576
MAX_DOCUMENTS = 100
SERVER_OWNED_METADATA = (
    "creationTimestamp",
    "deletionGracePeriodSeconds",
    "deletionTimestamp",
    "generation",
    "managedFields",
    "resourceVersion",
    "selfLink",
    "uid",
)
SENSITIVE_NAME = re.compile(
    r"(?:^|[_-])(password|passwd|token|api[_-]?key|access[_-]?key|secret|private[_-]?key|client[_-]?secret)(?:$|[_-])",
    re.IGNORECASE,
)
SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|token|apikey|accesskey|secretkey|privatekey|clientsecret)$",
    re.IGNORECASE,
)
SAFE_REFERENCE_KEYS = frozenset(
    {
        "secretname",
        "secretkeyref",
        "configmapkeyref",
        "valuefrom",
    }
)
SENSITIVE_GATE_PREFIXES = (
    "allow_",
    "disable_",
    "enable_",
    "require_",
    "use_",
    "validate_",
    "verify_",
)
BOOLEAN_GATE_LITERALS = frozenset({"0", "1", "false", "no", "off", "on", "true", "yes"})


@dataclass(frozen=True, order=True)
class ManifestIdentity:
    api_version: str
    kind: str
    namespace: str | None
    name: str


@dataclass(frozen=True)
class ManifestEditValidation:
    valid: bool
    changed: bool
    source_sha256: str
    desired_sha256: str
    diff: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def manifest_sha256(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def validate_manifest_source(
    source: str,
    *,
    selected_identity: ManifestIdentity,
) -> tuple[str, ...]:
    errors, documents = parse_documents(source, label="source")
    if errors:
        return errors
    assert documents is not None
    security = secret_safety_errors(documents)
    identities, identity_errors = manifest_identities(documents)
    if not selected_identity_present(selected_identity, identities):
        identity_errors.append("selected live resource is not declared by this manifest source")
    return tuple([*security, *identity_errors])


def validate_manifest_edit(
    source: str,
    desired: str,
    *,
    selected_identity: ManifestIdentity,
) -> ManifestEditValidation:
    source_digest = manifest_sha256(source)
    desired_digest = manifest_sha256(desired)
    source_errors, source_documents = parse_documents(source, label="source")
    desired_errors, desired_documents = parse_documents(desired, label="edited")
    errors = [*source_errors, *desired_errors]
    if source_documents is not None:
        errors.extend(secret_safety_errors(source_documents))
    if desired_documents is not None:
        errors.extend(secret_safety_errors(desired_documents))
    if source_documents is not None and desired_documents is not None and not errors:
        source_identities, source_identity_errors = manifest_identities(source_documents)
        desired_identities, desired_identity_errors = manifest_identities(desired_documents)
        errors.extend(source_identity_errors)
        errors.extend(desired_identity_errors)
        if source_identities != desired_identities:
            errors.append("resource identities cannot be added, removed, or renamed")
        if not selected_identity_present(selected_identity, source_identities):
            errors.append("selected live resource is not declared by this manifest source")
        errors.extend(server_owned_field_errors(source_documents, desired_documents))
    changed = source != desired
    if not changed:
        errors.append("manifest has no changes")
    # Never echo a source or diff after a secret-safety rejection.
    safe_to_render = not any("secret" in error.casefold() for error in errors)
    rendered_diff = unified_diff(source, desired) if safe_to_render and changed else ""
    return ManifestEditValidation(
        valid=not errors,
        changed=changed,
        source_sha256=source_digest,
        desired_sha256=desired_digest,
        diff=rendered_diff if not errors else "",
        errors=tuple(dict.fromkeys(errors)),
        warnings=("Safe PR 또는 직접 적용을 선택하기 전에 변경 diff와 영향을 확인하세요.",),
    )


def parse_documents(content: str, *, label: str) -> tuple[list[str], list[Any] | None]:
    if len(content.encode("utf-8")) > MAX_MANIFEST_BYTES:
        return [f"{label} manifest exceeds the 1 MiB editor limit"], None
    if "\x00" in content:
        return [f"{label} manifest contains an invalid NUL byte"], None
    try:
        tokens = list(yaml.scan(content))
    except yaml.YAMLError:
        return [f"{label} manifest is not valid YAML"], None
    if any(isinstance(token, AnchorToken | AliasToken) for token in tokens):
        return [f"{label} manifest anchors and aliases are not supported by the safe editor"], None
    try:
        documents = list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return [f"{label} manifest is not valid YAML"], None
    nonempty = [document for document in documents if document is not None]
    if not nonempty:
        return [f"{label} manifest contains no Kubernetes resources"], None
    if len(nonempty) > MAX_DOCUMENTS:
        return [f"{label} manifest exceeds the document limit"], None
    return [], nonempty


def manifest_identities(documents: Sequence[Any]) -> tuple[set[ManifestIdentity], list[str]]:
    identities: list[ManifestIdentity] = []
    errors: list[str] = []
    for document in documents:
        for resource in flattened_resources(document):
            identity = manifest_identity(resource)
            if identity is None:
                errors.append("every YAML document must be a named Kubernetes resource")
            else:
                identities.append(identity)
    if len(set(identities)) != len(identities):
        errors.append("manifest contains duplicate Kubernetes resource identities")
    return set(identities), errors


def flattened_resources(document: Any) -> list[Mapping[str, Any]]:
    if not isinstance(document, Mapping):
        return []
    if str(document.get("kind") or "") != "List":
        return [document]
    items = document.get("items")
    return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []


def manifest_identity(resource: Mapping[str, Any]) -> ManifestIdentity | None:
    metadata = resource.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    api_version = resource.get("apiVersion")
    kind = resource.get("kind")
    name = metadata.get("name")
    namespace = metadata.get("namespace")
    if (
        not isinstance(api_version, str)
        or not api_version.strip()
        or not isinstance(kind, str)
        or not kind.strip()
        or not isinstance(name, str)
        or not name.strip()
        or (namespace is not None and not isinstance(namespace, str))
    ):
        return None
    return ManifestIdentity(api_version, kind, namespace, name)


def selected_identity_present(
    selected: ManifestIdentity,
    identities: set[ManifestIdentity],
) -> bool:
    return any(
        identity.api_version == selected.api_version
        and identity.kind.casefold() == selected.kind.casefold()
        and identity.name == selected.name
        and identity.namespace in {selected.namespace, None}
        for identity in identities
    )


def secret_safety_errors(documents: Sequence[Any]) -> list[str]:
    for document in documents:
        for resource in flattened_resources(document):
            if str(resource.get("kind") or "").casefold() == "secret":
                return ["Secret resources cannot be displayed or edited"]
            if contains_sensitive_literal(resource):
                return ["manifest contains a secret-like literal and cannot be displayed or edited"]
    return []


def contains_sensitive_literal(value: Any) -> bool:
    if isinstance(value, Mapping):
        env_name = value.get("name")
        if (
            isinstance(env_name, str)
            and SENSITIVE_NAME.search(env_name)
            and "value" in value
            and value.get("value") not in (None, "")
            and not is_sensitive_gate_flag(env_name, value.get("value"))
        ):
            return True
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if (
                normalized not in SAFE_REFERENCE_KEYS
                and SENSITIVE_KEY.search(normalized)
                and child not in (None, "")
                and not isinstance(child, Mapping | list)
            ):
                return True
            if contains_sensitive_literal(child):
                return True
        return False
    if isinstance(value, list):
        return any(contains_sensitive_literal(item) for item in value)
    return False


def is_sensitive_gate_flag(name: str, value: Any) -> bool:
    """Allow explicit boolean feature gates without weakening secret detection.

    Kubernetes manifests commonly pair a secret-backed variable such as
    ``OPS_CONTROL_TOKEN`` with ``REQUIRE_CONTROL_TOKEN=true``.  The latter is a
    policy switch, not credential material.  Only an allowlisted gate prefix
    plus an exact boolean literal qualifies; arbitrary values remain blocked.
    """

    normalized_name = name.strip().casefold().replace("-", "_")
    if not normalized_name.startswith(SENSITIVE_GATE_PREFIXES):
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value in {0, 1}
    return isinstance(value, str) and value.strip().casefold() in BOOLEAN_GATE_LITERALS


def server_owned_field_errors(source: Sequence[Any], desired: Sequence[Any]) -> list[str]:
    source_by_identity = resource_map(source)
    desired_by_identity = resource_map(desired)
    for identity, source_resource in source_by_identity.items():
        desired_resource = desired_by_identity.get(identity)
        if desired_resource is None:
            continue
        if source_resource.get("status") != desired_resource.get("status"):
            return ["status is server-owned and cannot be changed through GitOps"]
        source_metadata = source_resource.get("metadata")
        desired_metadata = desired_resource.get("metadata")
        if not isinstance(source_metadata, Mapping) or not isinstance(desired_metadata, Mapping):
            continue
        if any(
            source_metadata.get(field) != desired_metadata.get(field)
            for field in SERVER_OWNED_METADATA
        ):
            return ["server-owned metadata cannot be changed through GitOps"]
    return []


def resource_map(documents: Sequence[Any]) -> dict[ManifestIdentity, Mapping[str, Any]]:
    result: dict[ManifestIdentity, Mapping[str, Any]] = {}
    for document in documents:
        for resource in flattened_resources(document):
            identity = manifest_identity(resource)
            if identity is not None:
                result[identity] = resource
    return result


def unified_diff(source: str, desired: str) -> str:
    return "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            desired.splitlines(keepends=True),
            fromfile="a/manifest.yaml",
            tofile="b/manifest.yaml",
        )
    )
