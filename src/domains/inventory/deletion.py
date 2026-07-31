"""Exact inventory resource cascade preview and audited delete dispatch."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from domains.command.events import CommandRequestedBody
from domains.command.router import (
    COMMAND_PRIORITY_HIGH,
    accept_command_with_receipt_stage,
    announce_staged_operation_event,
    command_accepted_response,
    publish_accepted_operation,
)
from domains.gitops.events import Diff
from domains.identity.dependencies import require_cluster_access, require_session
from packages.config.constants import Command, RiskLevel
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import ResourceDeleteRequest
from packages.contracts.gateway.responses import (
    ResourceDeletePreviewRef,
    ResourceDeletePreviewResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.parity import CommandReceipt
from packages.runtime.dependencies import get_db, get_events, get_operation_events

router = APIRouter()

MAX_CASCADE_DEPENDENTS = 200
CASCADE_UNAVAILABLE = "resource_delete_cascade_unavailable"
DELETE_CAPABILITY_UNAVAILABLE = "resource_delete_capability_unavailable"
DELETE_PREVIEW_STALE = "resource_delete_preview_stale"
IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"


@router.get(
    gateway_routes.RESOURCE_DELETE_PREVIEW_PATH,
    response_model=ResourceDeletePreviewResponse,
)
def get_resource_delete_preview(
    resource_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ResourceDeletePreviewResponse:
    """Return only a complete, bounded, same-snapshot owner-reference cascade."""

    context = _resource_context(db, current, resource_id, write=False)
    cascade_reader = getattr(db, "read_inventory_cascade", None)
    if not callable(cascade_reader):
        raise _conflict(CASCADE_UNAVAILABLE, "Cascade evidence repository is unavailable.")
    cascade = cascade_reader(
        workspace_id=context["workspace_id"],
        cluster_id=context["cluster_id"],
        resource=context["resource"],
        limit=MAX_CASCADE_DEPENDENTS,
    )
    if not isinstance(cascade, Mapping):
        raise _conflict(CASCADE_UNAVAILABLE, "Cascade evidence is invalid.")
    if (
        cascade.get("resources_complete") is not True
        or cascade.get("truncated") is not False
        or str(cascade.get("snapshot_id") or "") != str(context["resource"]["snapshot_id"])
    ):
        raise _conflict(
            CASCADE_UNAVAILABLE,
            "A complete bounded cascade is required before deletion.",
        )
    raw_dependents = cascade.get("dependents")
    if not isinstance(raw_dependents, list) or len(raw_dependents) > MAX_CASCADE_DEPENDENTS:
        raise _conflict(CASCADE_UNAVAILABLE, "Cascade dependent evidence exceeds its bound.")
    root = _preview_ref(context["resource"])
    dependents = [_preview_ref(item) for item in raw_dependents if isinstance(item, Mapping)]
    if len(dependents) != len(raw_dependents):
        raise _conflict(CASCADE_UNAVAILABLE, "Cascade dependent evidence is invalid.")
    revision = _preview_revision(
        workspace_id=context["workspace_id"],
        cluster_id=context["cluster_id"],
        snapshot_id=str(cascade["snapshot_id"]),
        root=root,
        dependents=dependents,
    )
    return ResourceDeletePreviewResponse(
        root=root,
        dependents=dependents,
        revision=revision,
        max_dependents=MAX_CASCADE_DEPENDENTS,
    )


@router.post(
    gateway_routes.RESOURCE_DELETE_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def request_resource_delete(
    resource_id: str,
    payload: ResourceDeleteRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    """Revalidate the preview and queue one exact foreground delete command."""

    context = _resource_context(db, current, resource_id, write=True)
    _require_delete_agent(db, context["workspace_id"], context["cluster_id"])
    preview = get_resource_delete_preview(resource_id, current, db)
    if preview.revision != payload.preview_revision:
        raise _conflict(DELETE_PREVIEW_STALE, "Resource or cascade changed after confirmation.")
    descriptor = _delete_descriptor(db, context, preview.root)
    request_fingerprint = _request_fingerprint(
        resource_id=resource_id,
        preview_revision=preview.revision,
        root=preview.root,
    )
    command_id = _command_id(
        context["workspace_id"],
        str(current.user_id),
        payload.idempotency_key,
    )
    replay = await _replay_receipt(
        db,
        workspace_id=context["workspace_id"],
        command_id=command_id,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay

    resource_payload = {**preview.root.model_dump(), "plural": descriptor["name"]}
    diff = Diff(
        workspace_id=context["workspace_id"],
        cluster_id=context["cluster_id"],
        resource=f"{preview.root.kind}/{preview.root.name}",
        namespace=preview.root.namespace or "",
        desired_image="",
        actual_image="resource-observed",
        risk=RiskLevel.REVIEW_REQUIRED,
        status="resource_delete",
        has_changes=True,
        changes=[{"operation": "delete", "resource": resource_payload}],
        basis={
            "preview_revision": preview.revision,
            "root": preview.root.model_dump(),
            "dependent_count": len(preview.dependents),
            "dependents": [item.model_dump() for item in preview.dependents],
        },
    )
    command = CommandRequestedBody(
        cluster_id=context["cluster_id"],
        action=Command.KUBERNETES_RESOURCE_DELETE_ACTION,
        namespace=preview.root.namespace or "",
        reason=payload.reason,
        diff=diff,
        command_id=command_id,
        payload={
            "request_fingerprint": request_fingerprint,
            "resources": [resource_payload],
            "propagation_policy": "Foreground",
            "cascade": {
                "preview_revision": preview.revision,
                "dependent_count": len(preview.dependents),
            },
        },
        workspace_id=context["workspace_id"],
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(current.user_id),
        direct_execution=True,
        direct_execution_confirmed=True,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(current.user_id, tuple(current.roles)),
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=command.workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


def _resource_context(
    db: Any,
    current: Any,
    resource_id: str,
    *,
    write: bool,
) -> dict[str, Any]:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    resource = db.get_inventory_resource_by_key(
        workspace_id=workspace_id,
        inventory_key=resource_id,
    )
    if not isinstance(resource, Mapping):
        raise HTTPException(status_code=404, detail="inventory resource not found")
    cluster_id = str(resource.get("cluster_id") or "")
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )
    if write:
        require_cluster_access(
            db,
            current,
            workspace_id,
            cluster_id,
            Permission.DEPLOY_RUN.value,
        )
    return {
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "resource": dict(resource),
    }


def _preview_ref(resource: Mapping[str, Any]) -> ResourceDeletePreviewRef:
    uid = str(resource.get("uid") or "").strip()
    resource_version = str(resource.get("resource_version") or "").strip()
    api_version = str(resource.get("api_version") or "").strip()
    kind = str(resource.get("kind") or "").strip()
    name = str(resource.get("name") or "").strip()
    if not all((uid, resource_version, api_version, kind, name)):
        raise _conflict(
            CASCADE_UNAVAILABLE,
            "Every delete target requires exact UID and resourceVersion evidence.",
        )
    api_group, separator, version = api_version.rpartition("/")
    if not separator:
        api_group, version = "", api_version
    return ResourceDeletePreviewRef(
        api_group=api_group,
        version=version,
        kind=kind,
        namespace=(str(resource["namespace"]) if resource.get("namespace") is not None else None),
        name=name,
        uid=uid,
        resource_version=resource_version,
    )


def _preview_revision(
    *,
    workspace_id: str,
    cluster_id: str,
    snapshot_id: str,
    root: ResourceDeletePreviewRef,
    dependents: list[ResourceDeletePreviewRef],
) -> str:
    encoded = json.dumps(
        {
            "workspace_id": workspace_id,
            "cluster_id": cluster_id,
            "snapshot_id": snapshot_id,
            "root": root.model_dump(),
            "dependents": [item.model_dump() for item in dependents],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _require_delete_agent(db: Any, workspace_id: str, cluster_id: str) -> None:
    reader = getattr(db, "list_cluster_agent_statuses", None)
    statuses = reader(workspace_id, cluster_id) if callable(reader) else []
    if not any(
        isinstance(item, Mapping)
        and str(item.get("status") or "").casefold() == "connected"
        and Command.KUBERNETES_RESOURCE_DELETE_CAPABILITY in set(item.get("capabilities") or ())
        for item in statuses
    ):
        raise _conflict(
            DELETE_CAPABILITY_UNAVAILABLE,
            "The exact resource delete agent capability is unavailable.",
        )


def _delete_descriptor(
    db: Any,
    context: Mapping[str, Any],
    root: ResourceDeletePreviewRef,
) -> Mapping[str, Any]:
    snapshot = db.latest_inventory_snapshot(context["workspace_id"], context["cluster_id"])
    snapshot_summary = snapshot.get("summary") if isinstance(snapshot, Mapping) else None
    source_summary = (
        snapshot_summary.get("summary") if isinstance(snapshot_summary, Mapping) else None
    )
    discovery = (
        source_summary.get("api_resource_discovery")
        if isinstance(source_summary, Mapping)
        else None
    )
    descriptors = discovery.get("resources") if isinstance(discovery, Mapping) else None
    if (
        discovery is None
        or discovery.get("completeness") != "exact"
        or not isinstance(descriptors, list)
    ):
        raise _conflict(DELETE_CAPABILITY_UNAVAILABLE, "API resource discovery is unavailable.")
    api_version = f"{root.api_group}/{root.version}" if root.api_group else root.version
    matches = [
        item
        for item in descriptors
        if isinstance(item, Mapping)
        and str(item.get("api_version") or "") == api_version
        and str(item.get("kind") or "").casefold() == root.kind.casefold()
        and item.get("namespaced") is (root.namespace is not None)
        and {"get", "delete"}.issubset(set(item.get("verbs") or ()))
        and str(item.get("name") or "")
    ]
    if len(matches) != 1:
        raise _conflict(
            DELETE_CAPABILITY_UNAVAILABLE,
            "No unique get/delete API descriptor exists for this resource.",
        )
    return matches[0]


def _request_fingerprint(
    *,
    resource_id: str,
    preview_revision: str,
    root: ResourceDeletePreviewRef,
) -> str:
    encoded = json.dumps(
        {
            "resource_id": resource_id,
            "preview_revision": preview_revision,
            "root": root.model_dump(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _command_id(workspace_id: str, user_id: str, idempotency_key: str) -> str:
    authority = "\0".join((workspace_id, user_id, idempotency_key))
    return f"cmd-delete-{hashlib.sha256(authority.encode()).hexdigest()[:24]}"


async def _replay_receipt(
    db: Any,
    *,
    workspace_id: str,
    command_id: str,
    request_fingerprint: str,
) -> CommandReceipt | None:
    reader = getattr(db, "get_agent_command", None)
    if not callable(reader):
        return None
    existing = reader(command_id, workspace_id)
    if inspect.isawaitable(existing):
        existing = await existing
    if not isinstance(existing, Mapping):
        return None
    plan = existing.get("payload")
    command_payload = plan.get("payload") if isinstance(plan, Mapping) else None
    if (
        not isinstance(command_payload, Mapping)
        or command_payload.get("request_fingerprint") != request_fingerprint
    ):
        raise _conflict(
            IDEMPOTENCY_KEY_REUSED,
            "Idempotency key was already used for another resource deletion.",
        )
    event_id = str(existing.get("confirmation_event_id") or "")
    correlation_id = str(existing.get("correlation_id") or "")
    if not event_id or not correlation_id:
        raise _conflict(IDEMPOTENCY_KEY_REUSED, "Stored delete receipt is incomplete.")
    return CommandReceipt(
        command_id=command_id,
        event_id=event_id,
        audit_event_id=event_id,
        correlation_id=correlation_id,
        status=str(existing.get("status") or "queued"),
    )


def _conflict(code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "detail": detail})
