"""Resolve a rendered Kustomize resource to one immutable repository file.

The resolver deliberately considers only files reachable from the bound local
Kustomize graph at the approved commit.  A missing or ambiguous identity is not
editable; callers must fail closed instead of guessing a repository path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import yaml

from domains.gitops.repository_discovery import (
    KUSTOMIZATION_FILES,
    GitHubClient,
    collect_kustomize_source_contents,
    normalize_kustomize_local_reference,
    parent_path,
    parse_kustomization_document,
    render_source_directory,
    sequence_value,
    string_sequence,
)
from domains.gitops.source_patch import (
    RAW_YAML,
    ManifestSourcePatchError,
    canonical_manifest_digest,
    field_path_segments,
    object_value_at,
)
from domains.manifest_editor.validation import (
    ManifestIdentity,
    flattened_resources,
    manifest_identity,
    parse_documents,
)


@dataclass(frozen=True)
class KustomizeEditSource:
    path: str
    source_type: str
    manifest_sha256: str
    document_identity: ManifestIdentity | None = None


class KustomizeSourceClient(GitHubClient, Protocol):
    """Structural alias used by non-discovery callers such as the SCM worker."""


async def resolve_unique_kustomize_edit_source(
    client: KustomizeSourceClient,
    *,
    repo_ref: str,
    revision: str,
    binding_manifest_path: str,
    selected_identity: ManifestIdentity,
    protected_field_paths: tuple[str, ...] = (),
) -> KustomizeEditSource | None:
    """Return one exact raw object file, or ``None`` when it is not provable."""

    tree, _warnings = await client.tree_at_revision(repo_ref, revision)
    source_dir = render_source_directory(binding_manifest_path, "kustomize")
    contents, _dependency_warnings = await collect_kustomize_source_contents(
        client,
        repo_ref,
        revision,
        source_dir,
        tree,
    )
    resource_paths, field_owned = kustomize_resource_paths_and_field_ownership(
        contents,
        selected_identity=selected_identity,
        protected_field_paths=protected_field_paths,
    )
    if field_owned:
        return None
    matches: list[KustomizeEditSource] = []
    for path, raw in sorted(contents.items()):
        if path.rsplit("/", 1)[-1] in KUSTOMIZATION_FILES:
            continue
        if path not in resource_paths:
            continue
        if not path.casefold().endswith((".yaml", ".yml", ".json")):
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        errors, documents = parse_documents(content, label=path)
        if errors or documents is None:
            continue
        for document in documents:
            # List wrappers remain unsupported because editing one child while
            # preserving the wrapper's raw byte layout is not yet provable.
            if not isinstance(document, Mapping):
                continue
            if str(document.get("kind") or "") == "List":
                if any(
                    (identity := manifest_identity(resource)) is not None
                    and identity_matches_selected(identity, selected_identity)
                    for resource in flattened_resources(document)
                ):
                    return None
                continue
            resource = document
            identity = manifest_identity(resource)
            if identity is None or not identity_matches_selected(identity, selected_identity):
                continue
            matches.append(
                KustomizeEditSource(
                    path=path,
                    source_type=RAW_YAML,
                    manifest_sha256=canonical_manifest_digest(resource),
                    document_identity=(identity if len(documents) > 1 else None),
                )
            )
    return matches[0] if len(matches) == 1 else None


def kustomize_resource_paths_and_field_ownership(
    contents: Mapping[str, bytes],
    *,
    selected_identity: ManifestIdentity,
    protected_field_paths: tuple[str, ...],
) -> tuple[set[str], bool]:
    resource_paths: set[str] = set()
    kustomization_directories = {
        parent_path(path)
        for path in contents
        if path.rsplit("/", 1)[-1] in KUSTOMIZATION_FILES
    }
    for path, content in sorted(contents.items()):
        if path.rsplit("/", 1)[-1] not in KUSTOMIZATION_FILES:
            continue
        document = parse_kustomization_document(content, path)
        directory = parent_path(path)
        for field in ("resources", "bases", "components"):
            for raw_reference in string_sequence(document.get(field), field):
                reference = normalize_kustomize_local_reference(
                    raw_reference,
                    field=field,
                    current_directory=directory,
                )
                if reference not in kustomization_directories:
                    resource_paths.add(reference)
        if kustomization_owns_protected_field(
            document,
            directory=directory,
            contents=contents,
            selected_identity=selected_identity,
            protected_field_paths=protected_field_paths,
        ):
            return resource_paths, True
    return resource_paths, False


def kustomization_owns_protected_field(
    document: Mapping[str, Any],
    *,
    directory: str,
    contents: Mapping[str, bytes],
    selected_identity: ManifestIdentity,
    protected_field_paths: tuple[str, ...],
) -> bool:
    if not protected_field_paths:
        return False
    # Custom transformers can mutate arbitrary fields and their target
    # semantics are plugin-defined. Without executing and attributing the
    # transformer, ownership of the protected field is not provable.
    if sequence_value(document.get("transformers"), "transformers"):
        return True
    if "spec.replicas" in protected_field_paths:
        raw_replicas = sequence_value(document.get("replicas"), "replicas")
        if any(not isinstance(replica, Mapping) for replica in raw_replicas):
            return True
        for replica in raw_replicas:
            assert isinstance(replica, Mapping)
            if str(replica.get("name") or "") == selected_identity.name:
                return True
    for field in ("patches", "patchesJson6902"):
        for entry in sequence_value(document.get(field), field):
            if not isinstance(entry, Mapping):
                return True
            target = entry.get("target")
            if not target_may_select(target, selected_identity):
                continue
            patch = entry.get("patch")
            path = entry.get("path")
            if isinstance(patch, str):
                if patch_content_owns_field(
                    patch,
                    selected_identity=selected_identity,
                    protected_field_paths=protected_field_paths,
                    target_declared=isinstance(target, Mapping),
                ):
                    return True
            elif isinstance(path, str):
                reference = normalize_kustomize_local_reference(
                    path,
                    field=f"{field}.path",
                    current_directory=directory,
                )
                raw = contents.get(reference)
                if raw is None:
                    return True
                try:
                    patch_text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    return True
                if patch_content_owns_field(
                    patch_text,
                    selected_identity=selected_identity,
                    protected_field_paths=protected_field_paths,
                    target_declared=isinstance(target, Mapping),
                ):
                    return True
            else:
                return True
    for raw_reference in string_sequence(
        document.get("patchesStrategicMerge"),
        "patchesStrategicMerge",
    ):
        if "\n" in raw_reference:
            patch_text = raw_reference
        else:
            reference = normalize_kustomize_local_reference(
                raw_reference,
                field="patchesStrategicMerge",
                current_directory=directory,
            )
            raw = contents.get(reference)
            if raw is None:
                return True
            try:
                patch_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return True
        if patch_content_owns_field(
            patch_text,
            selected_identity=selected_identity,
            protected_field_paths=protected_field_paths,
            target_declared=False,
        ):
            return True
    return replacements_own_field(
        document.get("replacements"),
        selected_identity,
        protected_field_paths,
    )


def target_may_select(target: object, selected: ManifestIdentity) -> bool:
    if target is None:
        return True
    if not isinstance(target, Mapping):
        return True
    exact = {
        "kind": selected.kind,
        "name": selected.name,
        "namespace": selected.namespace or "",
    }
    for key, expected in exact.items():
        value = target.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return True
        # Kustomize target names may be regular expressions. Only a plain,
        # unequal literal can prove that the patch targets another object.
        if value != expected:
            if all(char.isalnum() or char in "._-" for char in value):
                return False
            return True
    return True


def patch_content_owns_field(
    content: str,
    *,
    selected_identity: ManifestIdentity,
    protected_field_paths: tuple[str, ...],
    target_declared: bool,
) -> bool:
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError:
        return True
    if isinstance(payload, list):
        return any(
            isinstance(item, Mapping)
            and item.get("op") in {"add", "copy", "move", "remove", "replace"}
            and any(
                json_pointer_overlaps_field(str(item.get("path") or ""), path)
                or (
                    item.get("op") in {"copy", "move"}
                    and json_pointer_overlaps_field(str(item.get("from") or ""), path)
                )
                for path in protected_field_paths
            )
            for item in payload
        )
    if not isinstance(payload, Mapping):
        return True
    identity = manifest_identity(payload)
    if identity is None:
        if not target_declared:
            return True
    elif not identity_matches_selected(identity, selected_identity):
        return False
    return any(mapping_declares_field(payload, path) for path in protected_field_paths)


def json_pointer_overlaps_field(pointer: str, field_path: str) -> bool:
    protected = f"/{field_path.replace('.', '/')}"
    return bool(
        pointer == protected
        or pointer.startswith(f"{protected}/")
        or protected.startswith(f"{pointer}/")
    )


def mapping_declares_field(value: Mapping[str, Any], field_path: str) -> bool:
    segments = field_path_segments(field_path)
    if not segments:
        return True
    try:
        object_value_at(value, segments)
    except ManifestSourcePatchError:
        return False
    return True


def replacements_own_field(
    value: object,
    selected: ManifestIdentity,
    protected_field_paths: tuple[str, ...],
) -> bool:
    for replacement in sequence_value(value, "replacements"):
        if not isinstance(replacement, Mapping):
            return True
        for target in sequence_value(replacement.get("targets"), "replacements.targets"):
            if not isinstance(target, Mapping):
                return True
            select = target.get("select")
            if not target_may_select(select, selected):
                continue
            field_paths = target.get("fieldPaths")
            if not isinstance(field_paths, Sequence) or isinstance(field_paths, str | bytes):
                return True
            if any(not isinstance(path, str) for path in field_paths):
                return True
            if any(
                any(
                    semantic_fields_overlap(path, protected)
                    for protected in protected_field_paths
                )
                for path in field_paths
            ):
                return True
    return False


def semantic_fields_overlap(left: str, right: str) -> bool:
    return bool(
        left == right
        or left.startswith(f"{right}.")
        or right.startswith(f"{left}.")
    )


def identity_matches_selected(
    identity: ManifestIdentity,
    selected: ManifestIdentity,
) -> bool:
    return bool(
        identity.api_version == selected.api_version
        and identity.kind.casefold() == selected.kind.casefold()
        and identity.name == selected.name
        and identity.namespace in {selected.namespace, None}
    )
