"""Commit-pinned raw manifest를 재직렬화 없이 제한적으로 수정한다."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

from domains.manifest_editor.validation import ManifestIdentity, manifest_identity
from packages.contracts.remediation_source import (
    RemediationSourceContract,
    RemediationSourceDeclaration,
)

RAW_JSON = "raw-json"
RAW_YAML = "raw-yaml"
SUPPORTED_SOURCE_TYPES = frozenset({RAW_JSON, RAW_YAML})
IMAGE_PATCH_API_VERSION = "gitops.krafton.dev/v1alpha1"
IMAGE_PATCH_KIND = "GitOpsImagePatch"
SCALAR_PATCH_API_VERSION = "gitops.krafton.dev/v1alpha1"
SCALAR_PATCH_KIND = "GitOpsScalarPatch"
CONTAINER_LIST_KEYS = ("containers", "initContainers", "ephemeralContainers")
PLAIN_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]*$")
FIELD_PATH_SEGMENT_PATTERN = re.compile(r"^([A-Za-z0-9_-]+)(?:\[name=([^\]]+)\])?$")
MEMORY_QUANTITY_PATTERN = re.compile(r"^(\d+)(Ki|Mi|Gi)$")
ScalarValue = str | int | float | bool | None
DECLARED_SOURCE_STALE_VALUE_MESSAGE = (
    "declared source scalar does not match approved value"
)


class ManifestSourcePatchError(ValueError):
    """원문이 승인 snapshot과 다르거나 단일 image 치환을 보장할 수 없음."""


class RemediationSourcePatchUnsupported(ManifestSourcePatchError):
    """Repository contract does not declare the requested source mutation."""


@dataclass(frozen=True)
class ImageScalarReplacement:
    container_name: str
    current_image: str
    previous_image: str


@dataclass(frozen=True)
class ManifestImagePatchPlan:
    source_type: str
    source_manifest_sha256: str
    expected_base_sha: str
    manifest_path: str
    replacements: tuple[ImageScalarReplacement, ...]


@dataclass(frozen=True)
class ScalarFieldReplacement:
    field_path: str
    current_value: ScalarValue
    desired_value: ScalarValue


@dataclass(frozen=True)
class ManifestScalarPatchPlan:
    action_type: str
    source_type: str
    source_manifest_sha256: str
    expected_base_sha: str
    manifest_path: str
    replacements: tuple[ScalarFieldReplacement, ...]
    rollback_replacements: tuple[ScalarFieldReplacement, ...]


@dataclass(frozen=True)
class DeclaredScalarPatch:
    source_type: str
    source_path: str
    replacements: tuple[ScalarFieldReplacement, ...]
    document_identity: ManifestIdentity | None = None


@dataclass(frozen=True)
class _FieldPathSegment:
    key: str
    selected_name: str | None = None


def image_patch_content(plan: ManifestImagePatchPlan) -> str:
    """구버전 provider도 안전한 별도 파일로만 쓰는 비밀 없는 instruction 문서."""

    validate_image_patch_plan(plan)
    return yaml.safe_dump(
        {
            "apiVersion": IMAGE_PATCH_API_VERSION,
            "kind": IMAGE_PATCH_KIND,
            "spec": {
                "sourceType": plan.source_type,
                "sourceManifestSha256": plan.source_manifest_sha256,
                "expectedBaseSha": plan.expected_base_sha,
                "manifestPath": plan.manifest_path,
                "replacements": [
                    {
                        "containerName": item.container_name,
                        "currentImage": item.current_image,
                        "previousImage": item.previous_image,
                    }
                    for item in plan.replacements
                ],
            },
        },
        sort_keys=False,
        allow_unicode=True,
    )


def scalar_patch_content(plan: ManifestScalarPatchPlan) -> str:
    """권위 snapshot의 기존 scalar만 바꾸는 forward+rollback 계획을 직렬화한다."""

    validate_scalar_patch_plan(plan)
    return yaml.safe_dump(
        {
            "apiVersion": SCALAR_PATCH_API_VERSION,
            "kind": SCALAR_PATCH_KIND,
            "spec": {
                "actionType": plan.action_type,
                "sourceType": plan.source_type,
                "sourceManifestSha256": plan.source_manifest_sha256,
                "expectedBaseSha": plan.expected_base_sha,
                "manifestPath": plan.manifest_path,
                "replacements": [scalar_replacement_body(item) for item in plan.replacements],
                "rollbackReplacements": [
                    scalar_replacement_body(item) for item in plan.rollback_replacements
                ],
            },
        },
        sort_keys=False,
        allow_unicode=True,
    )


def scalar_replacement_body(item: ScalarFieldReplacement) -> dict[str, ScalarValue]:
    return {
        "fieldPath": item.field_path,
        "currentValue": item.current_value,
        "desiredValue": item.desired_value,
    }


def parse_image_patch_plan(content: str) -> ManifestImagePatchPlan | None:
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ManifestSourcePatchError("structured image patch document is invalid") from exc
    if not isinstance(payload, dict) or payload.get("kind") != IMAGE_PATCH_KIND:
        return None
    if payload.get("apiVersion") != IMAGE_PATCH_API_VERSION or set(payload) != {
        "apiVersion",
        "kind",
        "spec",
    }:
        raise ManifestSourcePatchError("structured image patch document is invalid")
    spec = payload.get("spec")
    if not isinstance(spec, dict) or set(spec) != {
        "sourceType",
        "sourceManifestSha256",
        "expectedBaseSha",
        "manifestPath",
        "replacements",
    }:
        raise ManifestSourcePatchError("structured image patch document is invalid")
    raw_replacements = spec["replacements"]
    if not isinstance(raw_replacements, list):
        raise ManifestSourcePatchError("structured image patch document is invalid")
    replacements: list[ImageScalarReplacement] = []
    for raw in raw_replacements:
        if not isinstance(raw, dict) or set(raw) != {
            "containerName",
            "currentImage",
            "previousImage",
        }:
            raise ManifestSourcePatchError("structured image patch document is invalid")
        if not all(isinstance(raw[key], str) for key in raw):
            raise ManifestSourcePatchError("structured image patch document is invalid")
        replacements.append(
            ImageScalarReplacement(
                container_name=raw["containerName"],
                current_image=raw["currentImage"],
                previous_image=raw["previousImage"],
            )
        )
    plan = ManifestImagePatchPlan(
        source_type=spec["sourceType"] if isinstance(spec["sourceType"], str) else "",
        source_manifest_sha256=(
            spec["sourceManifestSha256"] if isinstance(spec["sourceManifestSha256"], str) else ""
        ),
        expected_base_sha=(
            spec["expectedBaseSha"] if isinstance(spec["expectedBaseSha"], str) else ""
        ),
        manifest_path=spec["manifestPath"] if isinstance(spec["manifestPath"], str) else "",
        replacements=tuple(replacements),
    )
    validate_image_patch_plan(plan)
    return plan


def parse_scalar_patch_plan(content: str) -> ManifestScalarPatchPlan | None:
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ManifestSourcePatchError("structured scalar patch document is invalid") from exc
    if not isinstance(payload, dict) or payload.get("kind") != SCALAR_PATCH_KIND:
        return None
    if payload.get("apiVersion") != SCALAR_PATCH_API_VERSION or set(payload) != {
        "apiVersion",
        "kind",
        "spec",
    }:
        raise ManifestSourcePatchError("structured scalar patch document is invalid")
    spec = payload.get("spec")
    expected_keys = {
        "actionType",
        "sourceType",
        "sourceManifestSha256",
        "expectedBaseSha",
        "manifestPath",
        "replacements",
        "rollbackReplacements",
    }
    if not isinstance(spec, dict) or set(spec) != expected_keys:
        raise ManifestSourcePatchError("structured scalar patch document is invalid")
    replacements = parse_scalar_replacements(spec["replacements"])
    rollback = parse_scalar_replacements(spec["rollbackReplacements"])
    text_keys = (
        "actionType",
        "sourceType",
        "sourceManifestSha256",
        "expectedBaseSha",
        "manifestPath",
    )
    plan = ManifestScalarPatchPlan(
        action_type=spec["actionType"] if isinstance(spec["actionType"], str) else "",
        source_type=spec["sourceType"] if isinstance(spec["sourceType"], str) else "",
        source_manifest_sha256=(
            spec["sourceManifestSha256"] if isinstance(spec["sourceManifestSha256"], str) else ""
        ),
        expected_base_sha=(
            spec["expectedBaseSha"] if isinstance(spec["expectedBaseSha"], str) else ""
        ),
        manifest_path=spec["manifestPath"] if isinstance(spec["manifestPath"], str) else "",
        replacements=replacements,
        rollback_replacements=rollback,
    )
    if any(not isinstance(spec[key], str) for key in text_keys):
        raise ManifestSourcePatchError("structured scalar patch document is invalid")
    validate_scalar_patch_plan(plan)
    return plan


def parse_scalar_replacements(value: object) -> tuple[ScalarFieldReplacement, ...]:
    if not isinstance(value, list):
        raise ManifestSourcePatchError("structured scalar patch document is invalid")
    replacements: list[ScalarFieldReplacement] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "fieldPath",
            "currentValue",
            "desiredValue",
        }:
            raise ManifestSourcePatchError("structured scalar patch document is invalid")
        field_path = raw["fieldPath"]
        current = raw["currentValue"]
        desired = raw["desiredValue"]
        if (
            not isinstance(field_path, str)
            or not scalar_value(current)
            or not scalar_value(desired)
        ):
            raise ManifestSourcePatchError("structured scalar patch document is invalid")
        replacements.append(ScalarFieldReplacement(field_path, current, desired))
    return tuple(replacements)


def validate_image_patch_plan(plan: ManifestImagePatchPlan) -> None:
    if (
        plan.source_type != RAW_YAML
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan.source_manifest_sha256)
        or not re.fullmatch(r"[0-9a-f]{40,64}", plan.expected_base_sha)
        or not safe_manifest_path(plan.manifest_path)
        or len(plan.replacements) != 1
    ):
        raise ManifestSourcePatchError("structured image patch metadata is incomplete")
    replacement = plan.replacements[0]
    if (
        not replacement.container_name
        or not replacement.current_image
        or not replacement.previous_image
        or replacement.current_image == replacement.previous_image
    ):
        raise ManifestSourcePatchError("structured image patch metadata is incomplete")


def validate_scalar_patch_plan(plan: ManifestScalarPatchPlan) -> None:
    if (
        plan.action_type
        not in {
            "oom_memory",
            "image_rollback",
            "image_tag_fix",
            "replica_scale",
            "probe_fix",
            "selector_fix",
        }
        or plan.source_type != RAW_YAML
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan.source_manifest_sha256)
        or not re.fullmatch(r"[0-9a-f]{40,64}", plan.expected_base_sha)
        or not safe_manifest_path(plan.manifest_path)
        or not 1 <= len(plan.replacements) <= 4
        or len(plan.rollback_replacements) != len(plan.replacements)
    ):
        raise ManifestSourcePatchError("structured scalar patch metadata is incomplete")
    paths = [item.field_path for item in plan.replacements]
    if len(set(paths)) != len(paths):
        raise ManifestSourcePatchError("structured scalar patch target is duplicated")
    rollback_by_path = {item.field_path: item for item in plan.rollback_replacements}
    if len(rollback_by_path) != len(plan.rollback_replacements):
        raise ManifestSourcePatchError("structured scalar rollback is not an exact inverse")
    for item in plan.replacements:
        rollback = rollback_by_path.get(item.field_path)
        if (
            not field_path_segments(item.field_path)
            or not scalar_value(item.current_value)
            or not scalar_value(item.desired_value)
            or same_scalar(item.current_value, item.desired_value)
            or rollback is None
            or not same_scalar(rollback.current_value, item.desired_value)
            or not same_scalar(rollback.desired_value, item.current_value)
            or not action_allows_replacement(plan.action_type, item)
        ):
            raise ManifestSourcePatchError("structured scalar rollback is not an exact inverse")


def scalar_value(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def same_scalar(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def action_allows_replacement(action_type: str, item: ScalarFieldReplacement) -> bool:
    path = item.field_path
    if action_type in {"image_rollback", "image_tag_fix"}:
        return path.endswith(".image") and all(
            isinstance(value, str) and bool(value.strip())
            for value in (item.current_value, item.desired_value)
        )
    if action_type == "replica_scale":
        return (
            path == "spec.replicas"
            and type(item.current_value) is int
            and type(item.desired_value) is int
            and 1 <= item.current_value < item.desired_value <= 10
        )
    if action_type == "oom_memory":
        return (
            path.endswith(".resources.requests.memory") or path.endswith(".resources.limits.memory")
        ) and memory_increases_within_cap(item.current_value, item.desired_value)
    if action_type == "probe_fix":
        if path.endswith(".timeoutSeconds"):
            return (
                type(item.current_value) is int
                and type(item.desired_value) is int
                and 1 <= item.current_value < item.desired_value <= 30
            )
        if path.endswith(".httpGet.port"):
            return type(item.desired_value) is int and 1 <= item.desired_value <= 65535
        if path.endswith(".httpGet.path"):
            return isinstance(item.desired_value, str) and item.desired_value.startswith("/")
        return False
    if action_type == "selector_fix":
        return (
            path.startswith("spec.selector.matchLabels.")
            or path.startswith("spec.template.metadata.labels.")
        ) and all(
            isinstance(value, str) and bool(value.strip())
            for value in (item.current_value, item.desired_value)
        )
    return False


def memory_increases_within_cap(current: object, desired: object) -> bool:
    current_bytes = memory_quantity_bytes(current)
    desired_bytes = memory_quantity_bytes(desired)
    return (
        current_bytes is not None
        and desired_bytes is not None
        and (current_bytes < desired_bytes <= 4 * 1024**3)
    )


def memory_quantity_bytes(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = MEMORY_QUANTITY_PATTERN.fullmatch(value)
    if match is None:
        return None
    factor = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}[match.group(2)]
    return int(match.group(1)) * factor


def safe_manifest_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(
        value and not value.startswith("/") and "\\" not in value and ".." not in path.parts
    )


def canonical_manifest_digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def parse_single_manifest(source: str, source_type: str) -> dict[str, Any]:
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ManifestSourcePatchError("unsupported manifest source type")
    try:
        if source_type == RAW_JSON:
            value = json.loads(source)
        else:
            documents = list(yaml.safe_load_all(source))
            if len(documents) != 1:
                raise ManifestSourcePatchError("manifest source must contain one document")
            value = documents[0]
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ManifestSourcePatchError("manifest source is invalid") from exc
    if not isinstance(value, dict):
        raise ManifestSourcePatchError("manifest source must be one object")
    return value


def materialize_image_patch(
    source: str,
    *,
    source_type: str,
    expected_source_sha256: str,
    replacements: Sequence[ImageScalarReplacement],
) -> str:
    """검증된 원문에서 이름 있는 container image scalar만 byte-span 치환한다."""

    original = parse_single_manifest(source, source_type)
    if original.get("kind") != "Deployment":
        raise ManifestSourcePatchError("manifest source must be a Deployment")
    if canonical_manifest_digest(original) != expected_source_sha256:
        raise ManifestSourcePatchError("manifest source digest does not match approved artifact")
    if not replacements:
        raise ManifestSourcePatchError("manifest image replacement is missing")
    try:
        if any(isinstance(token, AnchorToken | AliasToken) for token in yaml.scan(source)):
            raise ManifestSourcePatchError("manifest anchors and aliases are not patchable")
        nodes = list(yaml.compose_all(source))
    except yaml.YAMLError as exc:
        raise ManifestSourcePatchError("manifest source is invalid") from exc
    if len(nodes) != 1 or not isinstance(nodes[0], MappingNode):
        raise ManifestSourcePatchError("manifest source must contain one object")

    node_containers = deployment_container_nodes(nodes[0])
    expected = deepcopy(original)
    object_containers = deployment_container_objects(expected)
    spans: list[tuple[int, int, str]] = []
    used_names: set[str] = set()
    for replacement in replacements:
        if (
            not replacement.container_name
            or not replacement.current_image
            or not replacement.previous_image
            or replacement.current_image == replacement.previous_image
            or replacement.container_name in used_names
        ):
            raise ManifestSourcePatchError("manifest image replacement is ambiguous")
        used_names.add(replacement.container_name)

        node_matches = [
            image
            for name, image in node_containers
            if name == replacement.container_name and image.value == replacement.current_image
        ]
        object_matches = [
            container
            for container in object_containers
            if container.get("name") == replacement.container_name
            and container.get("image") == replacement.current_image
        ]
        if len(node_matches) != 1 or len(object_matches) != 1:
            raise ManifestSourcePatchError("manifest image target is not unique")
        image_node = node_matches[0]
        encoded = encoded_scalar(image_node, replacement.previous_image)
        spans.append((image_node.start_mark.index, image_node.end_mark.index, encoded))
        object_matches[0]["image"] = replacement.previous_image

    if len({(start, end) for start, end, _ in spans}) != len(spans):
        raise ManifestSourcePatchError("manifest image target overlaps")
    patched = source
    for start, end, encoded in sorted(spans, reverse=True):
        patched = f"{patched[:start]}{encoded}{patched[end:]}"
    if parse_single_manifest(patched, source_type) != expected:
        raise ManifestSourcePatchError("manifest patch changed fields outside approved images")
    return patched


def declared_scalar_patch(
    plan: ManifestScalarPatchPlan,
    contract: RemediationSourceContract,
) -> DeclaredScalarPatch:
    """Resolve a semantic patch only through repository-declared source locations."""

    validate_scalar_patch_plan(plan)
    source = contract.source_for_manifest(plan.manifest_path)
    if source is None:
        raise _unsupported("manifest path is not declared")
    replacements = tuple(
        _declared_replacement(source, plan.action_type, item) for item in plan.replacements
    )
    paths = [item.field_path for item in replacements]
    if len(set(paths)) != len(paths):
        raise _unsupported("declared source target is ambiguous")
    return DeclaredScalarPatch(
        source_type=source.source_type,
        source_path=source.source_path,
        replacements=replacements,
    )


def declared_image_patch(
    plan: ManifestImagePatchPlan,
    contract: RemediationSourceContract,
) -> DeclaredScalarPatch:
    """Resolve the legacy image plan through the same repository declaration."""

    validate_image_patch_plan(plan)
    replacement = plan.replacements[0]
    semantic_path = f"spec.template.spec.containers[name={replacement.container_name}].image"
    scalar_plan = ManifestScalarPatchPlan(
        action_type="image_rollback",
        source_type=plan.source_type,
        source_manifest_sha256=plan.source_manifest_sha256,
        expected_base_sha=plan.expected_base_sha,
        manifest_path=plan.manifest_path,
        replacements=(
            ScalarFieldReplacement(
                semantic_path,
                replacement.current_image,
                replacement.previous_image,
            ),
        ),
        rollback_replacements=(
            ScalarFieldReplacement(
                semantic_path,
                replacement.previous_image,
                replacement.current_image,
            ),
        ),
    )
    return declared_scalar_patch(scalar_plan, contract)


def materialize_declared_scalar_patch(
    source: str,
    patch: DeclaredScalarPatch,
    *,
    allow_already_applied: bool = False,
) -> str:
    """Change only the exact scalar selected by a parsed repository contract.

    Recovery PR creation may be retried after a newer commit has already moved
    the target scalar to the approved desired value.  In that case the source is
    left unchanged so the caller can create an audit-only change document.  Any
    third value remains a real concurrency conflict and fails closed.
    """

    if patch.source_type not in {"raw-yaml", "helm-values", "kustomize"}:
        raise _unsupported("declared source adapter is unsupported")
    if not patch.replacements:
        raise _unsupported("declared source has no replacement")
    try:
        if any(isinstance(token, AnchorToken | AliasToken) for token in yaml.scan(source)):
            raise ManifestSourcePatchError("declared source anchors and aliases are not patchable")
        nodes = list(yaml.compose_all(source))
        originals = list(yaml.safe_load_all(source))
    except yaml.YAMLError as exc:
        raise ManifestSourcePatchError("declared source is invalid") from exc
    if len(nodes) != len(originals):
        raise ManifestSourcePatchError("declared source document structure is invalid")
    if patch.document_identity is None:
        if len(nodes) != 1:
            raise ManifestSourcePatchError("declared source must contain one object")
        document_index = 0
    else:
        matches = [
            index
            for index, document in enumerate(originals)
            if isinstance(document, Mapping)
            and manifest_identity(document) == patch.document_identity
        ]
        if len(matches) != 1:
            raise ManifestSourcePatchError("declared source document is missing or ambiguous")
        document_index = matches[0]
    original = originals[document_index]
    node_root = nodes[document_index]
    if not isinstance(original, dict) or not isinstance(node_root, MappingNode):
        raise ManifestSourcePatchError("declared source must contain one object")

    expected_documents = deepcopy(originals)
    expected = expected_documents[document_index]
    spans: list[tuple[int, int, str]] = []
    for replacement in patch.replacements:
        segments = field_path_segments(replacement.field_path)
        current = object_value_at(original, segments)
        if allow_already_applied and same_scalar(current, replacement.desired_value):
            set_object_value(expected, segments, replacement.desired_value)
            continue
        if not same_scalar(current, replacement.current_value):
            raise ManifestSourcePatchError(DECLARED_SOURCE_STALE_VALUE_MESSAGE)
        node = node_value_at(node_root, segments)
        if not isinstance(node, ScalarNode):
            raise ManifestSourcePatchError("declared source target is not one scalar")
        spans.append(
            (
                node.start_mark.index,
                node.end_mark.index,
                encoded_scalar_value(node, replacement.desired_value),
            )
        )
        set_object_value(expected, segments, replacement.desired_value)

    if len({(start, end) for start, end, _ in spans}) != len(spans):
        raise ManifestSourcePatchError("declared source targets overlap")
    patched = source
    for start, end, encoded in sorted(spans, reverse=True):
        patched = f"{patched[:start]}{encoded}{patched[end:]}"
    try:
        patched_documents = list(yaml.safe_load_all(patched))
    except yaml.YAMLError as exc:
        raise ManifestSourcePatchError("declared patch produced invalid YAML") from exc
    if patched_documents != expected_documents:
        raise ManifestSourcePatchError("declared patch changed fields outside approved scalars")
    return patched


def _declared_replacement(
    source: RemediationSourceDeclaration,
    action_type: str,
    replacement: ScalarFieldReplacement,
) -> ScalarFieldReplacement:
    if action_type in {"image_rollback", "image_tag_fix"}:
        return _declared_image_replacement(source, replacement)
    if action_type == "replica_scale" and source.replica_path is not None:
        return ScalarFieldReplacement(
            source.replica_path,
            replacement.current_value,
            replacement.desired_value,
        )
    if action_type == "probe_fix":
        target = source.probe_path(replacement.field_path)
        if target is not None:
            return ScalarFieldReplacement(
                target,
                replacement.current_value,
                replacement.desired_value,
            )
    raise _unsupported("action field is not declared")


def _declared_image_replacement(
    source: RemediationSourceDeclaration,
    replacement: ScalarFieldReplacement,
) -> ScalarFieldReplacement:
    current = replacement.current_value
    desired = replacement.desired_value
    if not isinstance(current, str) or not isinstance(desired, str):
        raise _unsupported("image replacement is not textual")
    if (
        source.source_type == "raw-yaml"
        and source.image_path is not None
        and replacement.field_path == source.image_path
    ):
        return ScalarFieldReplacement(source.image_path, current, desired)
    if source.source_type == "raw-yaml":
        raise _unsupported("raw image field is not declared")

    current_ref = _tagged_image(current)
    desired_ref = _tagged_image(desired)
    if current_ref is None or desired_ref is None or current_ref[0] != desired_ref[0]:
        raise _unsupported("tag adapter cannot change image repository or digest")
    repository = current_ref[0]
    if source.source_type == "helm-values" and source.image_tag_path is not None:
        target_path = source.image_tag_path
    elif source.source_type == "kustomize" and repository in source.image_names:
        target_path = f"images[name={repository}].newTag"
    else:
        raise _unsupported("image field is not declared")
    return ScalarFieldReplacement(target_path, current_ref[1], desired_ref[1])


def _tagged_image(value: str) -> tuple[str, str] | None:
    if not value or "@" in value or any(char.isspace() for char in value):
        return None
    separator = value.rfind(":")
    if separator <= value.rfind("/") or separator == len(value) - 1:
        return None
    repository = value[:separator]
    tag = value[separator + 1 :]
    if not repository or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}", tag) is None:
        return None
    return repository, tag


def _unsupported(reason: str) -> RemediationSourcePatchUnsupported:
    return RemediationSourcePatchUnsupported(f"remediation source patch unsupported: {reason}")


def materialize_scalar_patch(source: str, plan: ManifestScalarPatchPlan) -> str:
    """승인 원문에서 allowlist scalar span만 바꾸고 나머지 byte는 보존한다."""

    validate_scalar_patch_plan(plan)
    return _materialize_scalar_replacements(
        source,
        source_type=plan.source_type,
        expected_source_sha256=plan.source_manifest_sha256,
        replacements=plan.replacements,
    )


def materialize_scalar_rollback(
    source: str,
    plan: ManifestScalarPatchPlan,
    *,
    expected_source_sha256: str,
) -> str:
    """검증된 forward plan의 exact inverse를 현재 patched 원문에 적용한다."""

    validate_scalar_patch_plan(plan)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_source_sha256):
        raise ManifestSourcePatchError("rollback source digest is invalid")
    return _materialize_scalar_replacements(
        source,
        source_type=plan.source_type,
        expected_source_sha256=expected_source_sha256,
        replacements=plan.rollback_replacements,
    )


def _materialize_scalar_replacements(
    source: str,
    *,
    source_type: str,
    expected_source_sha256: str,
    replacements: tuple[ScalarFieldReplacement, ...],
) -> str:
    original = parse_single_manifest(source, source_type)
    if original.get("kind") != "Deployment":
        raise ManifestSourcePatchError("manifest source must be a Deployment")
    if canonical_manifest_digest(original) != expected_source_sha256:
        raise ManifestSourcePatchError("manifest source digest does not match approved artifact")
    try:
        if any(isinstance(token, AnchorToken | AliasToken) for token in yaml.scan(source)):
            raise ManifestSourcePatchError("manifest anchors and aliases are not patchable")
        nodes = list(yaml.compose_all(source))
    except yaml.YAMLError as exc:
        raise ManifestSourcePatchError("manifest source is invalid") from exc
    if len(nodes) != 1 or not isinstance(nodes[0], MappingNode):
        raise ManifestSourcePatchError("manifest source must contain one object")

    expected = deepcopy(original)
    spans: list[tuple[int, int, str]] = []
    for replacement in replacements:
        segments = field_path_segments(replacement.field_path)
        current = object_value_at(original, segments)
        if not same_scalar(current, replacement.current_value):
            raise ManifestSourcePatchError("manifest scalar does not match approved artifact")
        node = node_value_at(nodes[0], segments)
        if not isinstance(node, ScalarNode):
            raise ManifestSourcePatchError("manifest scalar target is not unique")
        spans.append(
            (
                node.start_mark.index,
                node.end_mark.index,
                encoded_scalar_value(node, replacement.desired_value),
            )
        )
        set_object_value(expected, segments, replacement.desired_value)

    if len({(start, end) for start, end, _ in spans}) != len(spans):
        raise ManifestSourcePatchError("manifest scalar target overlaps")
    patched = source
    for start, end, encoded in sorted(spans, reverse=True):
        patched = f"{patched[:start]}{encoded}{patched[end:]}"
    if parse_single_manifest(patched, source_type) != expected:
        raise ManifestSourcePatchError("manifest patch changed fields outside approved scalars")
    return patched


def scalar_patch_matches_manifest(
    plan: ManifestScalarPatchPlan,
    manifest: Mapping[str, Any],
) -> bool:
    """SCM 권위 검증용: forward의 현재값이 승인 snapshot과 정확히 같은지 확인한다."""

    try:
        validate_scalar_patch_plan(plan)
        return all(
            same_scalar(
                object_value_at(manifest, field_path_segments(item.field_path)),
                item.current_value,
            )
            for item in plan.replacements
        )
    except ManifestSourcePatchError:
        return False


def field_path_segments(value: str) -> tuple[_FieldPathSegment, ...]:
    segments: list[_FieldPathSegment] = []
    for raw in _field_path_parts(value):
        match = FIELD_PATH_SEGMENT_PATTERN.fullmatch(raw)
        if match is None:
            return ()
        selected_name = match.group(2)
        if selected_name is not None and not selected_name.strip():
            return ()
        segments.append(_FieldPathSegment(match.group(1), selected_name))
    return tuple(segments)


def _field_path_parts(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    bracket_depth = 0
    for index, char in enumerate(value):
        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                return ()
        elif char == "." and bracket_depth == 0:
            parts.append(value[start:index])
            start = index + 1
    if bracket_depth != 0:
        return ()
    parts.append(value[start:])
    return tuple(parts)


def object_value_at(root: object, segments: tuple[_FieldPathSegment, ...]) -> object:
    current = root
    for segment in segments:
        if not isinstance(current, Mapping) or segment.key not in current:
            raise ManifestSourcePatchError("manifest scalar target is missing")
        current = current[segment.key]
        if segment.selected_name is not None:
            if not isinstance(current, list):
                raise ManifestSourcePatchError("manifest scalar target is missing")
            matches = [
                item
                for item in current
                if isinstance(item, Mapping) and item.get("name") == segment.selected_name
            ]
            if len(matches) != 1:
                raise ManifestSourcePatchError("manifest scalar target is not unique")
            current = matches[0]
    return current


def set_object_value(
    root: dict[str, Any],
    segments: tuple[_FieldPathSegment, ...],
    value: ScalarValue,
) -> None:
    current: object = root
    for segment in segments[:-1]:
        if not isinstance(current, dict):
            raise ManifestSourcePatchError("manifest scalar target is missing")
        current = current[segment.key]
        if segment.selected_name is not None:
            matches = [
                item
                for item in current
                if isinstance(item, dict) and item.get("name") == segment.selected_name
            ]
            if len(matches) != 1:
                raise ManifestSourcePatchError("manifest scalar target is not unique")
            current = matches[0]
    final = segments[-1]
    if final.selected_name is not None or not isinstance(current, dict):
        raise ManifestSourcePatchError("manifest scalar target is missing")
    current[final.key] = value


def node_value_at(root: Node, segments: tuple[_FieldPathSegment, ...]) -> Node:
    current = root
    for segment in segments:
        if not isinstance(current, MappingNode):
            raise ManifestSourcePatchError("manifest scalar target is missing")
        value = mapping_value(current, segment.key)
        if value is None:
            raise ManifestSourcePatchError("manifest scalar target is missing")
        current = value
        if segment.selected_name is not None:
            if not isinstance(current, SequenceNode):
                raise ManifestSourcePatchError("manifest scalar target is missing")
            matches = [
                item
                for item in current.value
                if isinstance(item, MappingNode)
                and isinstance(mapping_value(item, "name"), ScalarNode)
                and mapping_value(item, "name").value == segment.selected_name
            ]
            if len(matches) != 1:
                raise ManifestSourcePatchError("manifest scalar target is not unique")
            current = matches[0]
    return current


def encoded_scalar_value(node: ScalarNode, value: ScalarValue) -> str:
    if isinstance(value, str):
        if node.style == "'":
            return f"'{value.replace(chr(39), chr(39) * 2)}'"
        if node.style == '"':
            return json.dumps(value, ensure_ascii=False)
        if node.style is None and "\n" not in value:
            encoded = value
            try:
                if yaml.safe_load(encoded) == value:
                    return encoded
            except yaml.YAMLError:
                pass
        return json.dumps(value, ensure_ascii=False)
    if node.style is not None:
        raise ManifestSourcePatchError("manifest scalar style is not patchable")
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def mapping_value(node: MappingNode, key: str) -> Node | None:
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            return value_node
    return None


def deployment_container_nodes(root: MappingNode) -> list[tuple[str, ScalarNode]]:
    spec = mapping_value(root, "spec")
    template = mapping_value(spec, "template") if isinstance(spec, MappingNode) else None
    pod_spec = mapping_value(template, "spec") if isinstance(template, MappingNode) else None
    if not isinstance(pod_spec, MappingNode):
        return []
    result: list[tuple[str, ScalarNode]] = []
    for key in CONTAINER_LIST_KEYS:
        sequence = mapping_value(pod_spec, key)
        if not isinstance(sequence, SequenceNode):
            continue
        for item in sequence.value:
            if not isinstance(item, MappingNode):
                continue
            name = mapping_value(item, "name")
            image = mapping_value(item, "image")
            if isinstance(name, ScalarNode) and isinstance(image, ScalarNode):
                result.append((name.value, image))
    return result


def deployment_container_objects(root: dict[str, Any]) -> list[dict[str, Any]]:
    spec = root.get("spec")
    template = spec.get("template") if isinstance(spec, dict) else None
    pod_spec = template.get("spec") if isinstance(template, dict) else None
    if not isinstance(pod_spec, dict):
        return []
    result: list[dict[str, Any]] = []
    for key in CONTAINER_LIST_KEYS:
        containers = pod_spec.get(key)
        if isinstance(containers, list):
            result.extend(item for item in containers if isinstance(item, dict))
    return result


def encoded_scalar(node: ScalarNode, value: str) -> str:
    if node.style is None and PLAIN_IMAGE_PATTERN.fullmatch(value):
        return value
    if node.style == "'":
        return f"'{value.replace(chr(39), chr(39) * 2)}'"
    if node.style == '"':
        return json.dumps(value, ensure_ascii=False)
    raise ManifestSourcePatchError("manifest image scalar style is not patchable")
