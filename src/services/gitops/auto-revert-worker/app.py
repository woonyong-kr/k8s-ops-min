"""auto-revert-worker — rollout.diagnosed -> guarded revert Safe PR request."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from domains.gitops.source_patch import (
    ImageScalarReplacement,
    ManifestImagePatchPlan,
    ManifestSourcePatchError,
    canonical_manifest_digest,
    image_patch_content,
)
from domains.rca.events import RolloutDiagnosedBody
from domains.scm.events import SafePrFilePatch, SafePrRequestedBody
from packages.config.constants import GitHub
from packages.config.settings import env
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import JsonObject
from packages.runtime.app import App, EventContext

app = App("auto-revert-worker")

AUTO_REVERT_ENABLED_ENV = "RECOVERY_ENABLE_AUTO_REVERT_PR"
AUTO_REVERT_TITLE_PREFIX = "[auto-revert]"
AUTO_REVERT_PATCH_DESCRIPTION = "automated revert to the previous healthy image"
NEXT_OBSERVE = "observe"
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
ROUND_TRIPPABLE_SOURCE_TYPES = frozenset({"raw-yaml"})
TRUSTED_SOURCE_ORIGINS = frozenset({"git_cache", "github_contents", "git_repo_path"})
SUPPORTED_MANIFEST_KINDS = frozenset({"Deployment"})
SENSITIVE_LITERAL_TOKENS = (
    "auth",
    "authorization",
    "pass",
    "passwd",
    "password",
    "secret",
    "secrets",
    "token",
    "apikey",
    "credential",
    "credentials",
)
UNUSABLE_PREVIOUS_IMAGES = frozenset({"unknown", "resource-not-inspected", "<missing>", "redacted"})
IMAGE_FIELD_PATTERN = re.compile(
    r"(?:containers|initContainers|ephemeralContainers)\[name=([^\]]+)]\.image$"
)


class AutoRevertStore(Protocol):
    async def get_workflow_identity_for_command(self, command_id: str) -> JsonObject | None: ...

    async def get_workflow_step_details(
        self, workflow_run_id: str, name: str
    ) -> JsonObject | None: ...

    async def get_application(
        self, workspace_id: str, application_id: str
    ) -> JsonObject | None: ...

    async def get_deployment_binding(
        self, workspace_id: str, binding_id: str
    ) -> JsonObject | None: ...

    async def get_manifest_artifact_provenance(
        self,
        workspace_id: str,
        binding_id: str,
        commit_sha: str,
        manifest_path: str,
        resource: str,
        artifact_digest: str,
    ) -> JsonObject | None: ...


@dataclass(frozen=True)
class RevertContext:
    resource: str
    current_image: str
    previous_image: str
    desired_manifest: JsonObject
    changes: list[dict[str, object]]
    workspace_id: str
    repository_id: str
    binding_id: str
    application_id: str
    workflow_run_id: str
    environment: str
    manifest_path: str
    repo_ref: str
    base_branch: str
    commit_sha: str
    source_type: str
    source_manifest_sha256: str


def auto_revert_enabled() -> bool:
    """Fail closed unless the opt-in flag has an explicit true value."""

    return env(AUTO_REVERT_ENABLED_ENV, "").strip().lower() in TRUE_VALUES


def mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def change_records(value: object) -> list[dict[str, object]]:
    return (
        [dict(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def last_approved_image(changes: list[dict[str, object]]) -> str:
    for change in changes:
        if not first_text(change.get("field_path")).endswith(".image"):
            continue
        candidate = first_text(change.get("old_desired"))
        if candidate and candidate not in UNUSABLE_PREVIOUS_IMAGES:
            return candidate
    return ""


def configured_source_type(binding: Mapping[str, object], application: Mapping[str, object]) -> str:
    deploy_policy = mapping(binding.get("deploy_policy"))
    application_metadata = mapping(application.get("metadata"))
    binding_source = first_text(
        deploy_policy.get("manifest_source"), deploy_policy.get("source_type")
    )
    application_source = first_text(application_metadata.get("source_type"))
    if not binding_source or (application_source and application_source != binding_source):
        return ""
    return binding_source


def source_path_supports_type(manifest_path: str, source_type: str) -> bool:
    if not manifest_path or "#" in manifest_path:
        return False
    suffix = PurePosixPath(manifest_path).suffix.lower()
    return (source_type == "raw-yaml" and suffix in {".yaml", ".yml"}) or (
        source_type == "raw-json" and suffix == ".json"
    )


def identifier_tokens(value: object) -> set[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value))
    return set(re.findall(r"[a-z0-9]+", separated.lower()))


def sensitive_name(value: object) -> bool:
    return bool(identifier_tokens(value).intersection(SENSITIVE_LITERAL_TOKENS))


def command_contains_sensitive_literal(value: object) -> bool:
    if not isinstance(value, list):
        return False
    return any(
        isinstance(item, str) and sensitive_name(item.split("=", 1)[0].lstrip("-"))
        for item in value
    )


def manifest_contains_sensitive_literals(value: object) -> bool:
    if isinstance(value, Mapping):
        kind = first_text(value.get("kind"))
        if kind == "Secret":
            return True
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if kind == "Secret" and normalized_key in {"data", "stringdata"} and bool(item):
                return True
            if normalized_key == "env" and isinstance(item, list):
                for entry in item:
                    if (
                        isinstance(entry, Mapping)
                        and sensitive_name(entry.get("name"))
                        and entry.get("value") not in (None, "")
                    ):
                        return True
            if normalized_key in {"args", "command"} and command_contains_sensitive_literal(item):
                return True
            if (
                sensitive_name(key)
                and not isinstance(item, (Mapping, list))
                and not isinstance(item, bool)
                and item not in (None, "")
            ):
                return True
            if manifest_contains_sensitive_literals(item):
                return True
    elif isinstance(value, list):
        return any(manifest_contains_sensitive_literals(item) for item in value)
    return False


async def load_revert_context(
    evt: RolloutDiagnosedBody,
    ctx: EventContext[AutoRevertStore],
) -> RevertContext | None:
    command_id = first_text(evt.details.get("command_id"))
    if not command_id or ctx.db is None:
        return None
    loaded_identity = await ctx.db.get_workflow_identity_for_command(command_id)
    if not isinstance(loaded_identity, Mapping):
        return None
    identity = dict(loaded_identity)
    identity_workspace_id = first_text(identity.get("workspace_id"))
    if not identity_workspace_id or identity_workspace_id != evt.workspace_id:
        return None
    workflow_run_id = first_text(identity.get("workflow_run_id"))
    if not workflow_run_id:
        return None
    loaded_diff = await ctx.db.get_workflow_step_details(workflow_run_id, "diff")
    if not isinstance(loaded_diff, Mapping):
        return None
    application_id = first_text(identity.get("application_id"))
    if not application_id:
        return None
    loaded_application = await ctx.db.get_application(identity_workspace_id, application_id)
    if not isinstance(loaded_application, Mapping):
        return None
    binding_id = first_text(identity.get("binding_id"))
    if not binding_id:
        return None
    loaded_binding = await ctx.db.get_deployment_binding(identity_workspace_id, binding_id)
    if not isinstance(loaded_binding, Mapping):
        return None
    application = dict(loaded_application)
    manifest_path = first_text(application.get("manifest_path"))
    commit_sha = first_text(identity.get("commit_sha"))
    resource = first_text(loaded_diff.get("resource"))
    artifact_digest = first_text(mapping(loaded_diff.get("basis")).get("artifact_digest"))
    if not all((manifest_path, commit_sha, resource, artifact_digest)):
        return None
    loaded_provenance = await ctx.db.get_manifest_artifact_provenance(
        identity_workspace_id,
        binding_id,
        commit_sha,
        manifest_path,
        resource,
        artifact_digest,
    )
    if not isinstance(loaded_provenance, Mapping):
        return None
    return revert_context_from(
        dict(loaded_diff),
        identity,
        application,
        dict(loaded_binding),
        dict(loaded_provenance),
        evt,
    )


def revert_context_from(
    payload: Mapping[str, object],
    identity: Mapping[str, object],
    application: Mapping[str, object],
    binding: Mapping[str, object],
    provenance: Mapping[str, object],
    evt: RolloutDiagnosedBody,
) -> RevertContext | None:
    basis = mapping(payload.get("basis"))
    image = mapping(payload.get("image"))
    gitops = mapping(payload.get("gitops"))
    desired_manifest = mapping(
        payload.get("desired_manifest") or payload.get("manifest") or payload.get("manifest_body")
    )
    changes = change_records(payload.get("changes"))
    current_image = first_text(
        payload.get("desired_image"), payload.get("current_image"), image.get("current")
    )
    previous_image = first_text(
        last_approved_image(changes),
    )
    workspace_id = first_text(identity.get("workspace_id"))
    payload_workspace_id = first_text(payload.get("workspace_id"))
    application_id = first_text(identity.get("application_id"))
    binding_id = first_text(identity.get("binding_id"))
    workflow_run_id = first_text(identity.get("workflow_run_id"))
    commit_sha = first_text(identity.get("commit_sha"))
    repository_id = first_text(application.get("repository_id"))
    manifest_path = first_text(application.get("manifest_path"))
    repo_ref = first_text(application.get("repo_ref"))
    repository_default_branch = first_text(application.get("default_branch"))
    application_workspace_id = first_text(application.get("workspace_id"))
    registered_application_id = first_text(application.get("application_id"))
    payload_repository_id = first_text(payload.get("repository_id"), gitops.get("repository_id"))
    payload_manifest_path = first_text(payload.get("manifest_path"), gitops.get("manifest_path"))
    payload_repo_ref = first_text(
        payload.get("repo_ref"), gitops.get("repository"), basis.get("repo_ref")
    )
    payload_base_branch = first_text(
        payload.get("base_branch"),
        payload.get("branch"),
        gitops.get("branch"),
        basis.get("branch"),
    )
    payload_application_id = first_text(payload.get("application_id"), gitops.get("application_id"))
    payload_binding_id = first_text(payload.get("binding_id"), gitops.get("binding_id"))
    payload_workflow_run_id = first_text(
        payload.get("workflow_run_id"), gitops.get("workflow_run_id")
    )
    payload_commit_sha = first_text(
        payload.get("commit_sha"), gitops.get("commit_sha"), basis.get("commit_sha")
    )
    resource = first_text(payload.get("resource"))
    artifact_digest = first_text(basis.get("artifact_digest"))
    identity_environment = first_text(identity.get("environment"))
    identity_cluster_id = first_text(identity.get("cluster_id"))
    application_name = first_text(application.get("name"))
    source_type = configured_source_type(binding, application)
    binding_deploy_policy = mapping(binding.get("deploy_policy"))
    provenance_source_type = first_text(provenance.get("source_type"))
    provenance_source_origin = first_text(provenance.get("source_origin"))
    provenance_branch = first_text(provenance.get("branch"))
    provenance_document_count = provenance.get("source_document_count")
    provenance_artifact_count = provenance.get("artifact_count")
    provenance_source_manifest_sha256 = first_text(provenance.get("source_manifest_sha256"))
    expected_artifact_path = f"{manifest_path}#{resource}"
    if (
        not desired_manifest
        or first_text(desired_manifest.get("kind")) not in SUPPORTED_MANIFEST_KINDS
        or manifest_contains_sensitive_literals(desired_manifest)
        or canonical_manifest_digest(desired_manifest) != artifact_digest
        or first_text(basis.get("old_desired_source")) != "last_approved_snapshot"
        or not current_image
        or not previous_image
        or previous_image in UNUSABLE_PREVIOUS_IMAGES
        or current_image == previous_image
        or not manifest_path
        or not resource
        or not artifact_digest
        or not repository_default_branch
        or not provenance_branch
        or workspace_id != evt.workspace_id
        or application_workspace_id != workspace_id
        or registered_application_id != application_id
        or first_text(binding.get("workspace_id")) != workspace_id
        or first_text(binding.get("binding_id")) != binding_id
        or first_text(binding.get("repository_id")) != repository_id
        or first_text(binding.get("manifest_path")) != manifest_path
        or first_text(binding.get("environment")) != identity_environment
        or first_text(binding.get("cluster_id")) != identity_cluster_id
        or first_text(binding.get("app_name")) != application_name
        or first_text(binding.get("status")) != "active"
        or not binding_deploy_policy
        or source_type not in ROUND_TRIPPABLE_SOURCE_TYPES
        or not source_path_supports_type(manifest_path, source_type)
        or provenance_source_type != source_type
        or provenance_source_origin not in TRUSTED_SOURCE_ORIGINS
        or provenance.get("source_is_file") is not True
        or isinstance(provenance_document_count, bool)
        or provenance_document_count != 1
        or isinstance(provenance_artifact_count, bool)
        or provenance_artifact_count != 1
        or not provenance_source_manifest_sha256.startswith("sha256:")
        or first_text(provenance.get("workspace_id")) != workspace_id
        or first_text(provenance.get("repository_id")) != repository_id
        or first_text(provenance.get("binding_id")) != binding_id
        or first_text(provenance.get("application_id")) != application_id
        or first_text(provenance.get("workflow_run_id")) != workflow_run_id
        or first_text(provenance.get("commit_sha")) != commit_sha
        or first_text(provenance.get("manifest_path")) != manifest_path
        or first_text(provenance.get("artifact_manifest_path")) != expected_artifact_path
        or first_text(provenance.get("artifact_digest")) != artifact_digest
        or first_text(provenance.get("repo_ref")) != repo_ref
        or first_text(provenance.get("environment")) != identity_environment
        or first_text(provenance.get("cluster_id")) != identity_cluster_id
        or (payload_workspace_id and payload_workspace_id != workspace_id)
        or (payload_repository_id and payload_repository_id != repository_id)
        or (payload_manifest_path and payload_manifest_path != manifest_path)
        or (payload_repo_ref and payload_repo_ref != repo_ref)
        or (payload_base_branch and payload_base_branch != provenance_branch)
        or (payload_application_id and payload_application_id != application_id)
        or (payload_binding_id and payload_binding_id != binding_id)
        or (payload_workflow_run_id and payload_workflow_run_id != workflow_run_id)
        or (payload_commit_sha and payload_commit_sha != commit_sha)
    ):
        return None

    context = RevertContext(
        resource=first_text(payload.get("resource"), identity.get("application_id"), "deployment"),
        current_image=current_image,
        previous_image=previous_image,
        desired_manifest=desired_manifest,
        changes=changes,
        workspace_id=workspace_id,
        repository_id=repository_id,
        binding_id=binding_id,
        application_id=application_id,
        workflow_run_id=workflow_run_id,
        environment=identity_environment,
        manifest_path=manifest_path,
        repo_ref=repo_ref,
        base_branch=provenance_branch,
        commit_sha=commit_sha,
        source_type=source_type,
        source_manifest_sha256=provenance_source_manifest_sha256,
    )
    required_pr_target = (
        context.repository_id,
        context.binding_id,
        context.application_id,
        context.workflow_run_id,
        context.manifest_path,
        context.repo_ref,
        context.base_branch,
        context.commit_sha,
    )
    return context if all(required_pr_target) else None


def image_revert_pairs(context: RevertContext) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for change in context.changes:
        field_path = first_text(change.get("field_path"))
        if not field_path.endswith(".image"):
            continue
        previous = first_text(change.get("old_desired"))
        current = first_text(change.get("after"), change.get("new_desired"), context.current_image)
        if (
            not previous
            or previous in UNUSABLE_PREVIOUS_IMAGES
            or not current
            or previous == current
        ):
            continue
        match = IMAGE_FIELD_PATTERN.search(field_path)
        if match is not None:
            pairs.append((match.group(1), current, previous))
    return pairs


def safe_pr_request(
    evt: RolloutDiagnosedBody,
    context: RevertContext,
    replacements: list[tuple[str, str, str]],
) -> SafePrRequestedBody | None:
    resource_name = context.resource.rsplit("/", 1)[-1] or "deployment"
    command_id = first_text(evt.details.get("command_id"))
    body = (
        "Rollout verification failed; this PR restores the previous healthy image.\n\n"
        "## Diagnosis\n\n"
        f"- diagnosis: {evt.diagnosis}\n"
        f"- next_action: {evt.next_action}\n"
        f"- command_id: {command_id}\n"
        f"- failed_image: `{context.current_image}`\n"
        f"- previous_healthy_image: `{context.previous_image}`\n"
    )
    container_name, current_image, previous_image = replacements[0]
    plan = ManifestImagePatchPlan(
        source_type=context.source_type,
        source_manifest_sha256=context.source_manifest_sha256,
        expected_base_sha=context.commit_sha,
        manifest_path=context.manifest_path,
        replacements=(
            ImageScalarReplacement(
                container_name=container_name,
                current_image=current_image,
                previous_image=previous_image,
            ),
        ),
    )
    try:
        content = image_patch_content(plan)
    except ManifestSourcePatchError:
        return None
    instruction_id = hashlib.sha256(context.workflow_run_id.encode()).hexdigest()[:32]
    return SafePrRequestedBody(
        # 무인 자동 원복은 사람 확인 없이 만들어지는 변경 — PR 리뷰 게이트를 유지한다.
        delivery="pull_request",
        title=f"{AUTO_REVERT_TITLE_PREFIX} {resource_name} rollout recovery",
        body=body,
        provider=GitHub.PROVIDER,
        patches=[
            SafePrFilePatch(
                path=f".gitops/safe-pr/patches/{instruction_id}.yaml",
                content=content,
                description=AUTO_REVERT_PATCH_DESCRIPTION,
            )
        ],
        workspace_id=context.workspace_id,
        repository_id=context.repository_id,
        binding_id=context.binding_id,
        application_id=context.application_id,
        workflow_run_id=context.workflow_run_id,
        environment=context.environment,
        manifest_path=context.manifest_path,
        repo_ref=context.repo_ref,
        base_branch=context.base_branch,
        commit_sha=context.commit_sha,
    )


@app.on(RolloutDiagnosedBody)
async def on_rollout_diagnosed(
    evt: RolloutDiagnosedBody,
    ctx: EventContext[AutoRevertStore],
) -> AsyncIterator[EventBody]:
    if not auto_revert_enabled() or evt.next_action == NEXT_OBSERVE:
        return
    context = await load_revert_context(evt, ctx)
    if context is None:
        return
    replacements = image_revert_pairs(context)
    if len(replacements) != 1:
        return
    request = safe_pr_request(evt, context, replacements)
    if request is not None:
        yield request


if __name__ == "__main__":
    app.run()
