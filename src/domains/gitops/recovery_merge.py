"""Authorization check for an exact, operator-merged recovery Safe PR."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from domains.gitops.events import Diff, GitWebhookReceivedBody
from domains.gitops.source_patch import (
    ManifestSourcePatchError,
    field_path_segments,
    object_value_at,
    same_scalar,
    scalar_value,
    set_object_value,
)

RECOVERY_DEPLOY_PENDING = "deploy_pending"
RECOVERY_SAFE_PR_ROUTE = "safe_pr"
ALREADY_CONVERGED = "already_converged"
INTENDED_CHANGE = "intended_change"
REPLICA_FIELD_PATH = "spec.replicas"


@dataclass(frozen=True)
class RecoveryMergeAuthorization:
    tracked: bool
    request: GitWebhookReceivedBody | None = None


def mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def text(value: object) -> str:
    return str(value or "").strip()


def positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def approved_change_contract(
    payload: Mapping[str, Any],
) -> dict[str, tuple[object, object]] | None:
    lifecycle = mapping(payload.get("lifecycle"))
    authorization = mapping(lifecycle.get("authorization"))
    raw_changes = authorization.get("changes")
    if not isinstance(raw_changes, list) or not 1 <= len(raw_changes) <= 4:
        return None
    approved: dict[str, tuple[object, object]] = {}
    for item in raw_changes:
        if not isinstance(item, Mapping):
            return None
        field_path = text(item.get("field_path"))
        current = item.get("current_value")
        desired = item.get("desired_value")
        if (
            not field_path
            or field_path in approved
            or not field_path_segments(field_path)
            or not scalar_value(current)
            or not scalar_value(desired)
            or same_scalar(current, desired)
        ):
            return None
        approved[field_path] = (current, desired)
    return approved


def approved_replica_count(payload: Mapping[str, Any]) -> int | None:
    approved = approved_change_contract(payload)
    if approved is None or REPLICA_FIELD_PATH not in approved:
        return None
    return positive_int(approved[REPLICA_FIELD_PATH][1])


def projected_parent_value(
    source: object,
    *,
    parent_path: str,
    approved: Mapping[str, tuple[object, object]],
) -> object:
    if not isinstance(source, Mapping):
        raise ManifestSourcePatchError("approved parent change is not an object")
    projected = deepcopy(dict(source))
    prefix = f"{parent_path}."
    for field_path, (current, desired) in approved.items():
        if not field_path.startswith(prefix):
            continue
        relative = field_path[len(prefix) :]
        segments = field_path_segments(relative)
        if (
            not segments
            or not same_scalar(object_value_at(projected, segments), current)
        ):
            raise ManifestSourcePatchError(
                "approved parent change does not match current value"
            )
        set_object_value(projected, segments, desired)
    return projected


def approved_change_matches_diff(
    change: Mapping[str, Any],
    approved: Mapping[str, tuple[object, object]],
) -> set[str] | None:
    field_path = text(change.get("field_path"))
    classification = text(change.get("classification"))
    matched = {
        path
        for path in approved
        if path == field_path or path.startswith(f"{field_path}.")
    }
    if not matched:
        return set() if classification == ALREADY_CONVERGED else None
    if classification not in {INTENDED_CHANGE, ALREADY_CONVERGED}:
        return None

    if matched == {field_path}:
        current, desired = approved[field_path]
        if classification == INTENDED_CHANGE:
            valid = (
                same_scalar(change.get("old_desired"), current)
                and same_scalar(change.get("before"), current)
                and same_scalar(change.get("after"), desired)
            )
        else:
            valid = (
                same_scalar(change.get("old_desired"), current)
                and same_scalar(change.get("before"), desired)
                and same_scalar(change.get("after"), desired)
            )
        return matched if valid else None

    scoped = {path: approved[path] for path in matched}
    old_desired = change.get("old_desired")
    before = change.get("before")
    after = change.get("after")
    try:
        if classification == INTENDED_CHANGE:
            valid = (
                old_desired == before
                and projected_parent_value(
                    before,
                    parent_path=field_path,
                    approved=scoped,
                )
                == after
            )
        else:
            valid = (
                projected_parent_value(
                    old_desired,
                    parent_path=field_path,
                    approved=scoped,
                )
                == before
                == after
            )
    except (KeyError, ManifestSourcePatchError, TypeError):
        return None
    return matched if valid else None


def recovery_diff_matches_approved_scope(
    payload: Mapping[str, Any],
    diff: Diff,
) -> bool:
    """Bind merge authorization to the exact approved scalar changes."""

    lifecycle = mapping(payload.get("lifecycle"))
    authorization = mapping(lifecycle.get("authorization"))
    target = mapping(authorization.get("target"))
    approved = approved_change_contract(payload)
    namespace = text(target.get("namespace"))
    resource_kind = text(target.get("resource_kind"))
    resource_name = text(target.get("resource_name"))
    diff_kind, separator, diff_name = text(diff.resource).partition("/")
    if (
        not namespace
        or not resource_kind
        or not resource_name
        or approved is None
        or diff.namespace != namespace
        or not separator
        or diff_kind.casefold() != resource_kind.casefold()
        or diff_name != resource_name
    ):
        return False

    desired = mapping(diff.desired_manifest)
    metadata = mapping(desired.get("metadata"))
    manifest_namespace = text(metadata.get("namespace"))
    if (
        text(desired.get("kind")).casefold() != resource_kind.casefold()
        or text(metadata.get("name")) != resource_name
        or manifest_namespace not in {"", namespace}
    ):
        return False
    try:
        if any(
            not same_scalar(
                object_value_at(desired, field_path_segments(field_path)),
                desired_value,
            )
            for field_path, (_, desired_value) in approved.items()
        ):
            return False
    except ManifestSourcePatchError:
        return False

    if not diff.has_changes:
        return diff.status in {"already_converged", "no_change"} and all(
            isinstance(raw_change, Mapping)
            and text(raw_change.get("classification")) == ALREADY_CONVERGED
            for raw_change in diff.changes
        )

    covered: set[str] = set()
    intended = 0
    for raw_change in diff.changes:
        if not isinstance(raw_change, Mapping):
            return False
        change = mapping(raw_change)
        matched = approved_change_matches_diff(change, approved)
        if matched is None:
            return False
        if matched and text(change.get("classification")) == INTENDED_CHANGE:
            intended += 1
        covered.update(matched)
    return intended > 0 and covered == set(approved)


async def exact_merged_recovery_request(
    db: Any,
    diff: Diff,
) -> GitWebhookReceivedBody | None:
    """Return only the server-stored request authorized by an exact PR merge.

    A base-branch push and a pull-request ``closed`` webhook can arrive in
    either order. The push may have already opened a generic approval for the
    deterministic workflow. We reuse the human PR review only when the
    recovery plan, PR/head, merge commit, binding and persisted workflow all
    agree with the immutable deployment request stored by the merge handler.
    """

    plan_reader = getattr(db, "get_recovery_plan_for_workflow", None)
    workflow_reader = getattr(db, "get_workflow_run", None)
    if not callable(plan_reader) or not callable(workflow_reader):
        return None
    record = await plan_reader(
        diff.workspace_id,
        diff.workflow_run_id,
        diff.binding_id,
        diff.application_id,
    )
    if not isinstance(record, Mapping):
        return None
    payload = mapping(record.get("payload"))
    lifecycle = mapping(payload.get("lifecycle"))
    pr = mapping(lifecycle.get("pr"))
    merge = mapping(lifecycle.get("merge"))
    raw_request = mapping(merge.get("deployment_request"))
    if (
        text(record.get("status")) != RECOVERY_DEPLOY_PENDING
        or not text(record.get("selected_action_id"))
        or text(payload.get("execution_route")) != RECOVERY_SAFE_PR_ROUTE
        or text(lifecycle.get("phase")) != RECOVERY_DEPLOY_PENDING
        or not recovery_diff_matches_approved_scope(payload, diff)
    ):
        return None
    try:
        request = cast(
            GitWebhookReceivedBody,
            GitWebhookReceivedBody.from_body(dict(raw_request)),
        )
    except (TypeError, ValueError):
        return None
    workflow = await workflow_reader(diff.workflow_run_id)
    if not isinstance(workflow, Mapping):
        return None

    pr_url = text(pr.get("url"))
    head_sha = text(pr.get("head_sha"))
    merge_commit_sha = text(merge.get("merge_commit_sha"))
    workflow_status = text(workflow.get("status"))
    approved_replicas = approved_replica_count(payload)
    if (
        not request.force
        or not request.image.strip()
        or type(request.replicas) is not int
        or request.replicas <= 0
        or (
            approved_replicas is not None
            and request.replicas != approved_replicas
        )
        or not pr_url
        or not head_sha
        or not merge_commit_sha
        or workflow_status
        not in {
            "started",
            "rendering",
            "diffing",
            "policy_checking",
            "waiting_for_approval",
        }
    ):
        return None

    expected = (
        (text(record.get("workspace_id")), diff.workspace_id),
        (text(record.get("correlation_id")), request.correlation_id),
        (text(merge.get("pr_url")), pr_url),
        (text(merge.get("head_sha")), head_sha),
        (merge_commit_sha, request.commit_sha),
        (text(merge.get("workflow_run_id")), request.workflow_run_id),
        (text(merge.get("repository_id")), request.repository_id),
        (text(merge.get("binding_id")), request.binding_id),
        (text(merge.get("application_id")), request.application_id),
        (text(merge.get("cluster_id")), request.cluster_id),
        (text(pr.get("repository_id")), request.repository_id),
        (text(pr.get("repo_ref")).casefold(), request.repo_ref.casefold()),
        (text(pr.get("base_branch")), request.branch),
        (text(pr.get("binding_id")), request.binding_id),
        (text(pr.get("application_id")), request.application_id),
        (text(pr.get("environment")), request.environment),
        (text(pr.get("cluster_id")), request.cluster_id),
        (text(pr.get("manifest_path")), request.manifest_path),
        (request.workspace_id, diff.workspace_id),
        (request.repository_id, diff.repository_id),
        (request.binding_id, diff.binding_id),
        (request.application_id, diff.application_id),
        (request.workflow_run_id, diff.workflow_run_id),
        (request.environment, diff.environment),
        (request.cluster_id, diff.cluster_id),
        (request.manifest_path, diff.manifest_path),
        (text(workflow.get("workflow_run_id")), request.workflow_run_id),
        (text(workflow.get("workspace_id")), request.workspace_id),
        (text(workflow.get("binding_id")), request.binding_id),
        (text(workflow.get("application_id")), request.application_id),
        (text(workflow.get("environment")), request.environment),
        (text(workflow.get("cluster_id")), request.cluster_id),
        (text(workflow.get("commit_sha")), request.commit_sha),
    )
    if any(not actual or actual != wanted for actual, wanted in expected):
        return None
    return request


async def recovery_merge_authorization(
    db: Any,
    diff: Diff,
) -> RecoveryMergeAuthorization:
    """Distinguish ordinary diffs from out-of-scope recovery-run diffs."""

    plan_reader = getattr(db, "get_recovery_plan_for_workflow", None)
    if not callable(plan_reader):
        return RecoveryMergeAuthorization(tracked=False)
    record = await plan_reader(
        diff.workspace_id,
        diff.workflow_run_id,
        diff.binding_id,
        diff.application_id,
    )
    if not isinstance(record, Mapping):
        return RecoveryMergeAuthorization(tracked=False)
    return RecoveryMergeAuthorization(
        tracked=True,
        request=await exact_merged_recovery_request(db, diff),
    )
