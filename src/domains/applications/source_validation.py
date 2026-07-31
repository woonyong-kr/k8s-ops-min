"""Persist revision-pinned repository connection evidence without claiming a deploy."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from packages.contracts.gateway.responses import (
    RepositoryManifestResource,
    RepositoryManifestValidationResponse,
)
from packages.contracts.gitops import (
    ManifestArtifactStatus,
    WorkflowRunStatus,
    WorkflowStepName,
    WorkflowStepStatus,
)


def validated_manifest_resources(
    validation: RepositoryManifestValidationResponse,
) -> tuple[RepositoryManifestResource, ...]:
    """Return complete, unique identities from one successful pinned validation."""

    resources = tuple(validation.resources)
    if not validation.valid or validation.resource_count < 1:
        raise ValueError("repository manifest validation produced no resources")
    if validation.resource_count != len(resources):
        raise ValueError("repository manifest validation resource count is incomplete")
    identities = [
        (
            resource.api_version.strip(),
            resource.kind.strip(),
            resource.namespace.strip() if resource.namespace is not None else None,
            resource.name.strip(),
        )
        for resource in resources
    ]
    if any(not api_version or not kind or not name for api_version, kind, _, name in identities):
        raise ValueError("repository manifest validation resource identity is incomplete")
    if len(set(identities)) != len(identities):
        raise ValueError("repository manifest validation resource identity is duplicated")
    return resources


def persist_repository_connect_validation(
    db: Any,
    *,
    workspace_id: str,
    repo_ref: str,
    branch: str,
    revision: str,
    source_type: str,
    validation: RepositoryManifestValidationResponse,
    application: Mapping[str, object],
    watch_target: Mapping[str, object],
    binding: Mapping[str, object],
) -> str:
    """Record source identities proven by the connect request's immutable revision.

    This run intentionally has a distinct identity from the deploy controller's
    workflow for the same commit.  It proves only repository access and rendering,
    so it must not prevent the controller from executing a later real deployment.
    """

    resources = validated_manifest_resources(validation)
    application_id = _required(application, "application_id")
    repository_id = _required(application, "repository_id")
    watch_target_id = _required(watch_target, "watch_target_id")
    binding_id = _required(binding, "binding_id")
    cluster_id = _required(binding, "cluster_id")
    environment = _required(binding, "environment")
    manifest_path = validation.manifest_path
    workflow_run_id = repository_connect_validation_workflow_id(
        workspace_id=workspace_id,
        binding_id=binding_id,
        revision=revision,
        manifest_path=manifest_path,
    )
    identity = {
        "workspace_id": workspace_id,
        "repository_id": repository_id,
        "watch_target_id": watch_target_id,
        "binding_id": binding_id,
        "application_id": application_id,
        "workflow_run_id": workflow_run_id,
        "environment": environment,
        "cluster_id": cluster_id,
        "commit_sha": revision,
        "manifest_path": manifest_path,
        "repo_ref": repo_ref,
        "branch": branch,
    }
    metadata = {
        "runtime_mode": "repository-connect-validation",
        "evidence_kind": "revision_pinned_manifest_validation",
        "source_type": source_type,
        "validation_mode": validation.validation_mode,
        "validated_resource_count": len(resources),
        "repository_revision": revision,
        "cluster_mutation": False,
    }
    db.start_workflow_run(
        {
            **identity,
            "status": WorkflowRunStatus.SUCCEEDED.value,
            "current_step": WorkflowStepName.RENDER.value,
            "summary": (
                f"Validated {len(resources)} repository resources at {revision[:12]}; "
                "no cluster mutation"
            ),
            "metadata": metadata,
        }
    )
    db.record_workflow_step(
        {
            **identity,
            "name": WorkflowStepName.RENDER.value,
            "status": WorkflowStepStatus.SUCCEEDED.value,
            "message": "Revision-pinned repository source rendered and validated",
            "details": {
                "repo_ref": repo_ref,
                "branch": branch,
                "commit_sha": revision,
                "manifest_path": manifest_path,
                "source_type": source_type,
                "validation_mode": validation.validation_mode,
                "resource_count": len(resources),
                "warnings": list(validation.warnings),
                "cluster_mutation": False,
            },
        }
    )

    source_summary = {
        "repo_ref": repo_ref,
        "branch": branch,
        "manifest_path": manifest_path,
        "source_type": source_type,
        "source_origin": "repository_connect_validation",
        "source_document_count": len(resources),
        "validation_mode": validation.validation_mode,
        "cluster_id": cluster_id,
        "application_id": application_id,
        "workflow_run_id": workflow_run_id,
        "environment": environment,
        "repository_revision": revision,
        "cluster_mutation": False,
    }
    for resource in resources:
        rendered_manifest = {
            "apiVersion": resource.api_version,
            "kind": resource.kind,
            "metadata": {
                "name": resource.name,
                **({"namespace": resource.namespace} if resource.namespace is not None else {}),
            },
        }
        resource_identity = "/".join(
            (
                resource.api_version,
                resource.kind.casefold(),
                resource.namespace or "_cluster",
                resource.name,
            )
        )
        suffix = hashlib.sha256(resource_identity.encode()).hexdigest()[:20]
        db.record_manifest_artifact(
            {
                **identity,
                "manifest_path": f"{manifest_path}#resource-{suffix}",
                "status": ManifestArtifactStatus.RENDERED.value,
                "rendered_manifest": rendered_manifest,
                "source_summary": {
                    **source_summary,
                    "resource_identity": resource_identity,
                },
            }
        )
    return workflow_run_id


def repository_connect_validation_workflow_id(
    *,
    workspace_id: str,
    binding_id: str,
    revision: str,
    manifest_path: str,
) -> str:
    authority = "\0".join(
        ("repository-connect-validation-v1", workspace_id, binding_id, revision, manifest_path)
    )
    return f"workflow-connect-validation-{hashlib.sha256(authority.encode()).hexdigest()[:24]}"


def _required(value: Mapping[str, object], key: str) -> str:
    normalized = str(value.get(key) or "").strip()
    if not normalized:
        raise ValueError(f"repository connection {key} is incomplete")
    return normalized
