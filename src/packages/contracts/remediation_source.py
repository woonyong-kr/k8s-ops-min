"""Strict repository-owned contract for remediation source locations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

REMEDIATION_SOURCE_API_VERSION = "remediation.opsia.dev/v1alpha1"
REMEDIATION_SOURCE_KIND = "RemediationSource"
REMEDIATION_SOURCE_CONTRACT_PATH = ".remediation.yaml"
MAX_CONTRACT_BYTES = 64 * 1024
SOURCE_TYPES = frozenset({"raw-yaml", "helm-values", "kustomize"})
PROBE_FIELD_SUFFIXES = frozenset(
    {
        "readinessProbe.httpGet.path",
        "readinessProbe.httpGet.port",
        "readinessProbe.timeoutSeconds",
        "livenessProbe.httpGet.path",
        "livenessProbe.httpGet.port",
        "livenessProbe.timeoutSeconds",
    }
)
FIELD_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(?:\[name=[^\[\]]+\])?$")
IMAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*[A-Za-z0-9]$")
REPO_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")


class RemediationSourceContractError(ValueError):
    """The repository contract is malformed or ambiguous."""


@dataclass(frozen=True)
class RemediationSourceDeclaration:
    manifest_path: str
    source_type: str
    source_path: str
    image_path: str | None = None
    image_tag_path: str | None = None
    image_names: tuple[str, ...] = ()
    replica_path: str | None = None
    probe_paths: tuple[tuple[str, str], ...] = ()

    def probe_path(self, semantic_field: str) -> str | None:
        return dict(self.probe_paths).get(semantic_field)


@dataclass(frozen=True)
class RemediationSourceContract:
    sources: tuple[RemediationSourceDeclaration, ...]

    def source_for_manifest(self, manifest_path: str) -> RemediationSourceDeclaration | None:
        matches = [source for source in self.sources if source.manifest_path == manifest_path]
        return matches[0] if len(matches) == 1 else None


def parse_remediation_source_contract(content: str) -> RemediationSourceContract:
    """Parse one strict, alias-free contract document."""

    if not content or len(content.encode("utf-8")) > MAX_CONTRACT_BYTES:
        raise RemediationSourceContractError("remediation source contract size is invalid")
    try:
        if any(isinstance(token, AnchorToken | AliasToken) for token in yaml.scan(content)):
            raise RemediationSourceContractError(
                "remediation source contract anchors and aliases are unsupported"
            )
        documents = list(yaml.compose_all(content))
        if len(documents) != 1 or not isinstance(documents[0], MappingNode):
            raise RemediationSourceContractError(
                "remediation source contract must contain one mapping"
            )
        _reject_duplicate_mapping_keys(documents[0])
        payload = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise RemediationSourceContractError("remediation source contract is invalid YAML") from exc
    if not isinstance(payload, dict) or set(payload) != {"apiVersion", "kind", "spec"}:
        raise RemediationSourceContractError("remediation source contract root is invalid")
    if (
        payload.get("apiVersion") != REMEDIATION_SOURCE_API_VERSION
        or payload.get("kind") != REMEDIATION_SOURCE_KIND
    ):
        raise RemediationSourceContractError("remediation source contract version is unsupported")
    spec = payload.get("spec")
    if not isinstance(spec, dict) or set(spec) != {"sources"}:
        raise RemediationSourceContractError("remediation source contract spec is invalid")
    raw_sources = spec.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise RemediationSourceContractError("remediation source contract sources are missing")

    sources = tuple(_parse_source(value) for value in raw_sources)
    manifest_paths = [source.manifest_path for source in sources]
    if len(set(manifest_paths)) != len(manifest_paths):
        raise RemediationSourceContractError("remediation source manifestPath is duplicated")
    return RemediationSourceContract(sources=sources)


def _parse_source(value: object) -> RemediationSourceDeclaration:
    if not isinstance(value, dict):
        raise RemediationSourceContractError("remediation source entry is invalid")
    source_type = value.get("sourceType")
    if source_type not in SOURCE_TYPES:
        raise RemediationSourceContractError("remediation source type is unsupported")
    common = {"manifestPath", "sourceType", "path"}
    allowed_by_type = {
        "raw-yaml": common | {"imagePath", "replicaPath", "probePaths"},
        "helm-values": common | {"imageTagPath", "replicaPath", "probePaths"},
        "kustomize": common | {"images"},
    }
    if not common.issubset(value) or not set(value).issubset(allowed_by_type[source_type]):
        raise RemediationSourceContractError("remediation source entry keys are invalid")

    manifest_path = _safe_repo_path(value.get("manifestPath"), "manifestPath")
    source_path = _safe_repo_path(value.get("path"), "path")
    if source_path == REMEDIATION_SOURCE_CONTRACT_PATH:
        raise RemediationSourceContractError("remediation source contract cannot patch itself")
    image_path = _optional_field_path(value.get("imagePath"), "imagePath")
    image_tag_path = _optional_field_path(value.get("imageTagPath"), "imageTagPath")
    replica_path = _optional_field_path(value.get("replicaPath"), "replicaPath")
    probe_paths = _parse_probe_paths(value.get("probePaths"))
    image_names = _parse_image_names(value.get("images"))

    declared_paths = [
        path
        for path in (image_path, image_tag_path, replica_path, *(path for _, path in probe_paths))
        if path is not None
    ]
    if len(set(declared_paths)) != len(declared_paths):
        raise RemediationSourceContractError("remediation source field path is duplicated")
    if source_type == "raw-yaml" and not (image_path or replica_path or probe_paths):
        raise RemediationSourceContractError("raw remediation source declares no fields")
    if source_type == "helm-values" and not (image_tag_path or replica_path or probe_paths):
        raise RemediationSourceContractError("Helm remediation source declares no fields")
    if source_type == "kustomize" and not image_names:
        raise RemediationSourceContractError("Kustomize remediation source declares no images")
    return RemediationSourceDeclaration(
        manifest_path=manifest_path,
        source_type=source_type,
        source_path=source_path,
        image_path=image_path,
        image_tag_path=image_tag_path,
        image_names=image_names,
        replica_path=replica_path,
        probe_paths=probe_paths,
    )


def _safe_repo_path(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise RemediationSourceContractError(f"remediation source {field} must be text")
    path = PurePosixPath(value)
    if (
        not value
        or value.strip() != value
        or value.startswith("/")
        or value.endswith("/")
        or value.startswith("./")
        or "//" in value
        or "\\" in value
        or "\x00" in value
        or REPO_PATH_PATTERN.fullmatch(value) is None
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise RemediationSourceContractError(f"remediation source {field} is unsafe")
    return value


def _optional_field_path(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _valid_field_path(value):
        raise RemediationSourceContractError(f"remediation source {field} is invalid")
    return value


def _valid_field_path(value: str) -> bool:
    return bool(value and all(FIELD_SEGMENT_PATTERN.fullmatch(part) for part in value.split(".")))


def _parse_probe_paths(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or not value:
        raise RemediationSourceContractError("remediation source probePaths is invalid")
    paths: list[tuple[str, str]] = []
    for semantic_field, path in value.items():
        if (
            not isinstance(semantic_field, str)
            or not _valid_field_path(semantic_field)
            or not any(semantic_field.endswith(suffix) for suffix in PROBE_FIELD_SUFFIXES)
            or not isinstance(path, str)
        ):
            raise RemediationSourceContractError("remediation source probe field is unsupported")
        if not _valid_field_path(path):
            raise RemediationSourceContractError("remediation source probePaths is invalid")
        paths.append((semantic_field, path))
    return tuple(paths)


def _parse_image_names(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise RemediationSourceContractError("remediation source images are invalid")
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name"}:
            raise RemediationSourceContractError("remediation source image entry is invalid")
        name = item.get("name")
        if not isinstance(name, str) or "@" in name or not IMAGE_NAME_PATTERN.fullmatch(name):
            raise RemediationSourceContractError("remediation source image name is invalid")
        names.append(name)
    if len(set(names)) != len(names):
        raise RemediationSourceContractError("remediation source image name is duplicated")
    return tuple(names)


def _reject_duplicate_mapping_keys(node: Node) -> None:
    if isinstance(node, MappingNode):
        keys: set[str] = set()
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode) or key_node.value in keys:
                raise RemediationSourceContractError(
                    "remediation source contract mapping key is duplicated or invalid"
                )
            keys.add(key_node.value)
            _reject_duplicate_mapping_keys(value_node)
    elif isinstance(node, SequenceNode):
        for item in node.value:
            _reject_duplicate_mapping_keys(item)
