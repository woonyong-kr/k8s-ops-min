"""Safe PR 이벤트 파이프라인 헬퍼."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from domains.gitops.repository import (
    derive_application_id,
    derive_deployment_binding_id,
    derive_repository_id,
    derive_workflow_run_id,
)
from domains.rca.events import SafePrPatchPreparedBody
from domains.scm.events import SafePrRequestedBody


def safe_pr_patch_sha256(patches: list[Any]) -> str:
    payload = [
        {
            "path": safe_pr_patch_field(patch, "path"),
            "content": safe_pr_patch_field(patch, "content"),
        }
        for patch in patches
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def safe_pr_patch_field(patch: Any, field: str) -> str:
    if isinstance(patch, Mapping):
        return str(patch.get(field) or "")
    return str(getattr(patch, field, "") or "")


def normalize_safe_pr_request(evt: SafePrRequestedBody) -> SafePrRequestedBody:
    payload = evt.to_body()
    repository_id = derive_repository_id(payload)
    binding_id = derive_deployment_binding_id({**payload, "repository_id": repository_id})
    application_id = derive_application_id(
        {**payload, "repository_id": repository_id, "binding_id": binding_id}
    )
    workflow_run_id = derive_workflow_run_id(
        {
            **payload,
            "repository_id": repository_id,
            "binding_id": binding_id,
            "application_id": application_id,
        }
    )
    return replace(
        evt,
        repository_id=repository_id,
        binding_id=binding_id,
        application_id=application_id,
        workflow_run_id=workflow_run_id,
        patch_sha256=safe_pr_patch_sha256(evt.patches),
    )


def patch_prepared_body(request: SafePrRequestedBody) -> SafePrPatchPreparedBody:
    return SafePrPatchPreparedBody(
        title=request.title,
        body=request.body,
        patch={
            "provider": request.provider,
            "pr_kind": request.pr_kind,
            "repository_id": request.repository_id,
            "repo_ref": request.repo_ref,
            "base_branch": request.base_branch,
            "manifest_path": request.manifest_path,
            "commit_sha": request.commit_sha,
            "patch_sha256": request.patch_sha256,
            "approval_ref": request.approval_ref,
            "policy_decision_ref": request.policy_decision_ref,
            "patches": [patch.to_body() for patch in request.patches],
        },
        provider=request.provider,
        request=request.to_body(),
        workspace_id=request.workspace_id,
        repository_id=request.repository_id,
        binding_id=request.binding_id,
        application_id=request.application_id,
        workflow_run_id=request.workflow_run_id,
        environment=request.environment,
        manifest_path=request.manifest_path,
        pr_kind=request.pr_kind,
        approval_ref=request.approval_ref,
        policy_decision_ref=request.policy_decision_ref,
        next_alert=request.next_alert.to_body() if request.next_alert is not None else None,
    )
