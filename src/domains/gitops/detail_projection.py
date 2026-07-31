"""Pure, provider-neutral GitOps application-detail projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from packages.contracts.gitops import WorkflowRunStatus
from packages.contracts.gitops.detail import (
    GitOpsActionCapability,
    GitOpsApplicationDetail,
    GitOpsApplicationDetailResponse,
    GitOpsApplicationScope,
    GitOpsDesiredLiveDiffAvailability,
    GitOpsOperationObservation,
    GitOpsSource,
)
from packages.contracts.parity import ClusterScope, ResourceRef

JsonObject = dict[str, Any]

_ACTIVE_WORKFLOW_STATUSES = frozenset(
    {
        WorkflowRunStatus.STARTED.value,
        WorkflowRunStatus.RENDERING.value,
        WorkflowRunStatus.DIFFING.value,
        WorkflowRunStatus.POLICY_CHECKING.value,
        WorkflowRunStatus.WAITING_FOR_APPROVAL.value,
        WorkflowRunStatus.APPLYING.value,
        WorkflowRunStatus.ROLLOUT_WAITING.value,
    }
)


def gitops_application_detail(
    application: Mapping[str, Any],
    *,
    bindings: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    can_refresh: bool,
    can_sync: bool,
) -> GitOpsApplicationDetailResponse:
    """Materialize only data observed by the product's own GitOps workflow.

    There is deliberately no desired/live diff payload and no guessed provider
    state: the application repository and Opsia workflow history are the only
    sources this projection consumes.
    """

    application_id = _text(application.get("application_id"))
    name = _text(application.get("name")) or application_id
    scope = _scope(application, bindings)
    source_revision = _source_revision(bindings, runs)
    operation = _operation(runs)
    sync_blocked = operation.in_progress is True
    return GitOpsApplicationDetailResponse(
        application=GitOpsApplicationDetail(
            application_id=application_id,
            name=name,
            resource=ResourceRef(
                api_group="opsia.io",
                version="v1",
                kind="GitOpsApplication",
                namespace=_unambiguous_namespace(bindings),
                name=name,
                uid=application_id,
            ),
            scope=scope,
            source=GitOpsSource(
                repository_ref=_optional_text(application.get("repo_ref")),
                default_branch=_optional_text(application.get("default_branch")),
                manifest_path=_optional_text(application.get("manifest_path")),
            ),
            desired_live_diff=GitOpsDesiredLiveDiffAvailability(
                availability="unavailable",
                source_revision=source_revision,
                live_observation_revision=None,
                reason_code=(
                    "live_observation_not_integrated"
                    if source_revision
                    else "source_revision_unavailable"
                ),
            ),
            operation=operation,
            capabilities=(
                _capability("refresh", can_refresh, operation_blocked=False),
                _capability("sync", can_sync, operation_blocked=sync_blocked),
            ),
        )
    )


def _scope(
    application: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
) -> GitOpsApplicationScope:
    usable = [binding for binding in bindings if _text(binding.get("cluster_id"))]
    if not usable:
        return GitOpsApplicationScope(
            availability="unavailable",
            scope=None,
            reason_code="binding_scope_unavailable",
        )
    target_keys = {
        (
            _text(binding.get("cluster_id")),
            _text(binding.get("namespace")),
        )
        for binding in usable
    }
    if len(target_keys) != 1:
        return GitOpsApplicationScope(
            availability="partial",
            scope=None,
            reason_code="multiple_target_scopes",
        )
    cluster_id, namespace = next(iter(target_keys))
    return GitOpsApplicationScope(
        availability="available",
        scope=ClusterScope(
            workspace_id=_text(application.get("workspace_id")),
            cluster_id=cluster_id,
            namespaces=(namespace,) if namespace else (),
            freshness="partial",
        ),
        reason_code=None,
    )


def _source_revision(
    bindings: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
) -> str | None:
    ordered_runs = sorted(runs, key=_timestamp_key, reverse=True)
    for run in ordered_runs:
        if revision := _optional_text(run.get("commit_sha")):
            return revision
    for binding in bindings:
        poll = binding.get("gitops_poll")
        if isinstance(poll, Mapping) and (
            revision := _optional_text(poll.get("last_seen_commit_sha"))
        ):
            return revision
        if revision := _optional_text(binding.get("watch_last_seen_commit_sha")):
            return revision
    return None


def _operation(runs: Sequence[Mapping[str, Any]]) -> GitOpsOperationObservation:
    observed = [run for run in runs if _optional_text(run.get("workflow_run_id"))]
    if not observed:
        return GitOpsOperationObservation(
            availability="unavailable",
            reason_code="workflow_operation_unobserved",
        )
    latest = max(observed, key=_timestamp_key)
    status = _optional_text(latest.get("status"))
    return GitOpsOperationObservation(
        availability="partial",
        in_progress=bool(status and status.casefold() in _ACTIVE_WORKFLOW_STATUSES),
        workflow_run_id=_optional_text(latest.get("workflow_run_id")),
        status=status,
        observed_at=_optional_text(latest.get("updated_at")),
        reason_code="provider_operation_not_integrated",
    )


def _capability(
    action: str,
    authorized: bool,
    *,
    operation_blocked: bool,
) -> GitOpsActionCapability:
    if not authorized:
        return GitOpsActionCapability(
            action=action,  # type: ignore[arg-type]
            authorization="denied",
            availability="unavailable",
            enabled=False,
            operation_blocked=False,
            reason_code="not_authorized",
        )
    if operation_blocked:
        return GitOpsActionCapability(
            action=action,  # type: ignore[arg-type]
            authorization="allowed",
            availability="unavailable",
            enabled=False,
            operation_blocked=True,
            reason_code="operation_in_progress",
        )
    return GitOpsActionCapability(
        action=action,  # type: ignore[arg-type]
        authorization="allowed",
        availability="unavailable",
        enabled=False,
        operation_blocked=False,
        reason_code=f"provider_{action}_not_integrated",
    )


def _unambiguous_namespace(bindings: Sequence[Mapping[str, Any]]) -> str | None:
    namespaces = {_optional_text(binding.get("namespace")) for binding in bindings}
    non_null = {namespace for namespace in namespaces if namespace is not None}
    return next(iter(non_null)) if len(non_null) == 1 else None


def _timestamp_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _text(value.get("updated_at")) or _text(value.get("created_at")),
        _text(value.get("workflow_run_id")),
    )


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_text(value: object) -> str | None:
    return _text(value) or None
