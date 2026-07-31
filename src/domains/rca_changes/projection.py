"""GitOps/SCM 이벤트를 안전한 workload change 행으로 변환."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from domains.gitops.events import WorkflowRunCompletedBody
from domains.scm.events import SafePrCreatedBody
from packages.config.constants import CommandStatus
from packages.config.settings import env
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gitops import DEFAULT_GITHUB_WEB_BASE, GITHUB_WEB_BASE_ENV
from packages.runtime.app import EventContext


def workload_change_row(
    evt: WorkflowRunCompletedBody,
    ctx: EventContext[Any],
    authority: Mapping[str, object],
) -> JsonObject | None:
    if not _same_workspace(evt.workspace_id, ctx.workspace_id):
        return None
    if any(
        _text(left) != _text(right)
        for left, right in (
            (evt.workspace_id, authority.get("workspace_id")),
            (evt.workflow_run_id, authority.get("workflow_run_id")),
            (evt.application_id, authority.get("application_id")),
            (evt.binding_id, authority.get("binding_id")),
        )
    ):
        return None
    result = _mapping(authority.get("command_result"))
    if not result:
        result = _mapping(_mapping(authority.get("run_metadata")).get("result"))
    resources = result.get("resources")
    resource_list = resources if isinstance(resources, list) else []
    rollout = _mapping(result.get("rollout"))
    failed_resources = [
        item
        for item in resource_list
        if isinstance(item, Mapping)
        and (item.get("applied") is False or _text(item.get("status")).casefold() == "failed")
    ]
    if (
        _text(result.get("status")) != CommandStatus.COMPLETED
        or result.get("applied") is False
        or (isinstance(failed_resources, list) and failed_resources)
        or rollout.get("ready") is False
    ):
        return None
    if any(
        isinstance(item, Mapping)
        and (item.get("applied") is False or _text(item.get("status")).casefold() == "failed")
        for item in resource_list
    ):
        return None
    command_id = _text(authority.get("command_id"))
    command_ids = evt.details.get("command_ids")
    completed_ids = {
        _text(item) for item in command_ids if isinstance(item, str)
    } if isinstance(command_ids, list) else {_text(evt.details.get("command_id"))}
    if not command_id or command_id not in completed_ids:
        return None
    diff = _mapping(authority.get("diff_details"))
    if any(
        _text(left) != _text(right)
        for left, right in (
            (diff.get("workspace_id"), authority.get("workspace_id")),
            (diff.get("repository_id"), authority.get("repository_id")),
            (diff.get("binding_id"), authority.get("binding_id")),
            (diff.get("workflow_run_id"), authority.get("workflow_run_id")),
            (diff.get("cluster_id"), authority.get("cluster_id")),
            (diff.get("commit_sha"), authority.get("commit_sha")),
            (diff.get("manifest_path"), authority.get("manifest_path")),
        )
    ):
        return None
    if diff.get("has_changes") is False or _text(diff.get("status")) == "no_change":
        return None
    resource_kind, resource_name = _resource_key(diff.get("resource"))
    namespace = _text(diff.get("namespace"))
    binding_namespace = _text(authority.get("namespace"))
    if namespace != binding_namespace:
        return None
    changed_at = _timestamp(ctx.created_at)
    required = (
        ctx.event_id,
        authority.get("workspace_id"),
        authority.get("cluster_id"),
        namespace,
        resource_kind,
        resource_name,
        authority.get("repository_id"),
        authority.get("binding_id"),
        authority.get("manifest_path"),
        authority.get("workflow_run_id"),
        authority.get("commit_sha"),
        authority.get("repo_ref"),
        changed_at,
    )
    if any(not _text(value) if isinstance(value, str) else value is None for value in required):
        return None
    return {
        "event_id": f"{ctx.event_id}:{command_id}",
        "workspace_id": _text(authority.get("workspace_id")),
        "cluster_id": _text(authority.get("cluster_id")),
        "namespace": namespace,
        "resource_kind": resource_kind,
        "resource_name": resource_name,
        "repository_id": _text(authority.get("repository_id")),
        "binding_id": _text(authority.get("binding_id")),
        "manifest_path": _text(authority.get("manifest_path")),
        "repo_ref": _text(authority.get("repo_ref")),
        "commit_sha": _text(authority.get("commit_sha")),
        "workflow_run_id": _text(authority.get("workflow_run_id")),
        "image_before": _optional_text(diff.get("actual_image")),
        "image_after": _optional_text(diff.get("desired_image")),
        "diff_details": dict(diff),
        "changed_at": changed_at,
    }


def workflow_pr_reference_row(
    evt: SafePrCreatedBody,
    ctx: EventContext[Any],
    authority: Mapping[str, object],
) -> JsonObject | None:
    if not _same_workspace(evt.workspace_id, ctx.workspace_id):
        return None
    if any(
        _text(left) != _text(right)
        for left, right in (
            (evt.workspace_id, authority.get("workspace_id")),
            (evt.workflow_run_id, authority.get("workflow_run_id")),
            (evt.application_id, authority.get("application_id")),
            (evt.binding_id, authority.get("binding_id")),
            (evt.repository_id, authority.get("repository_id")),
            (evt.commit_sha, authority.get("commit_sha")),
            (evt.manifest_path, authority.get("manifest_path")),
            (evt.repo_ref, authority.get("repo_ref")),
        )
    ):
        return None
    required = (
        authority.get("workspace_id"),
        authority.get("workflow_run_id"),
        authority.get("repository_id"),
        authority.get("binding_id"),
        authority.get("commit_sha"),
        authority.get("manifest_path"),
        authority.get("repo_ref"),
        evt.pr_url,
        ctx.event_id,
        ctx.created_at,
    )
    observed_at = _timestamp(ctx.created_at)
    if any(not _text(value) for value in required[:-1]) or observed_at is None:
        return None
    if not trusted_pr_url(evt.pr_url, authority.get("repo_ref")):
        return None
    return {
        "workspace_id": _text(authority.get("workspace_id")),
        "repository_id": _text(authority.get("repository_id")),
        "binding_id": _text(authority.get("binding_id")),
        "workflow_run_id": _text(authority.get("workflow_run_id")),
        "commit_sha": _text(authority.get("commit_sha")),
        "manifest_path": _text(authority.get("manifest_path")),
        "source_event_id": ctx.event_id,
        "pr_url": evt.pr_url,
        "observed_at": observed_at,
    }


def _same_workspace(body_workspace_id: object, envelope_workspace_id: object) -> bool:
    return bool(_text(body_workspace_id)) and _text(body_workspace_id) == _text(
        envelope_workspace_id
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return _text(value) or None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _resource_key(value: object) -> tuple[str, str]:
    raw = _text(value)
    if "/" not in raw:
        return "", ""
    kind, name = (_text(part) for part in raw.split("/", 1))
    if kind.casefold() == "unknown" or name.casefold() == "unknown":
        return "", ""
    return kind.casefold(), name


def trusted_pr_url(value: object, repo_ref: object) -> bool:
    parsed = urlparse(_text(value))
    allowed = urlparse(env(GITHUB_WEB_BASE_ENV, DEFAULT_GITHUB_WEB_BASE))
    try:
        same_origin = parsed.hostname == allowed.hostname and parsed.port == allowed.port
    except ValueError:
        return False
    expected_repo = [part for part in _text(repo_ref).strip("/").split("/") if part]
    path = [part for part in parsed.path.strip("/").split("/") if part]
    return bool(
        parsed.scheme == "https"
        and same_origin
        and not parsed.username
        and not parsed.password
        and len(expected_repo) == 2
        and len(path) == 4
        and path[:2] == expected_repo
        and path[2] == "pull"
        and path[3].isdigit()
    )
