"""Read-only, provider-neutral GitOps application detail API.

This router deliberately has no webhook HMAC dependency: it is a browser
projection guarded by the normal session and resource-authorization boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from domains.command.events import CommandRequestedBody
from domains.command.router import (
    COMMAND_PRIORITY_HIGH,
    accept_command_with_receipt_stage,
    announce_staged_operation_event,
    command_accepted_response,
    publish_accepted_operation,
    replay_resource_action_receipt,
    resource_action_command_id,
)
from domains.gitops.detail_projection import gitops_application_detail
from domains.gitops.events import Diff
from domains.gitops.resource_projection import (
    GITOPS_INVENTORY_QUERY_LIMIT,
    gitops_resource_insights,
    gitops_resource_tree,
    gitops_source_observation,
    provider_for_inventory_resource,
)
from domains.identity.dependencies import (
    require_cluster_access,
    require_resource_access,
    require_session,
    resolve_allowed_application_ids,
    resolve_allowed_cluster_ids,
)
from domains.target.connectivity import AGENT_STATUS_ONLINE, cluster_connection_status
from packages.config.constants import Command, RiskLevel
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gitops.detail import (
    GitOpsApplicationDetailResponse,
    GitOpsResourceActionRequest,
    GitOpsResourceInsightsResponse,
    GitOpsResourceTreeResponse,
)
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    AccessResourceType,
    Permission,
)
from packages.contracts.parity import CommandReceipt
from packages.runtime.dependencies import get_db, get_events, get_operation_events

router = APIRouter()

GITOPS_RESOURCE_TYPE = "custom_resource"
GITOPS_CAPABILITY_STALE = "gitops_capability_revision_stale"
GITOPS_IDEMPOTENCY_REUSED = "gitops_idempotency_key_reused"


@router.get(
    gateway_routes.GITOPS_APPLICATION_DETAIL_PATH,
    response_model=GitOpsApplicationDetailResponse,
)
async def get_gitops_application_detail(
    application_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> GitOpsApplicationDetailResponse:
    """Return observed source/workflow evidence without impersonating a provider.

    ``refresh`` and ``sync`` are capabilities, not action endpoints in this
    slice.  They stay disabled until a provider-neutral command executor has
    been integrated and can issue an auditable ``CommandReceipt``.
    """

    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_resource_access(
        db,
        current,
        workspace_id,
        AccessResourceType.APPLICATION.value,
        application_id,
        Permission.APPLICATION_READ.value,
    )
    application = await asyncio.to_thread(db.get_application, workspace_id, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    all_bindings = await asyncio.to_thread(
        db.list_application_deployment_bindings,
        workspace_id,
        application_id,
        limit=500,
    )
    inventory_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    visible_bindings = _visible_bindings(all_bindings, inventory_clusters)
    visible_runs = await _visible_runs(
        db,
        workspace_id=workspace_id,
        application_id=application_id,
        allowed_cluster_ids=inventory_clusters,
    )
    actions_authorized = await asyncio.to_thread(
        _actions_authorized,
        db,
        current,
        workspace_id,
        application_id,
        all_bindings,
    )
    return gitops_application_detail(
        application,
        bindings=visible_bindings,
        runs=visible_runs,
        can_refresh=actions_authorized,
        can_sync=actions_authorized,
    )


@router.get(
    gateway_routes.GITOPS_RESOURCE_TREE_PATH,
    response_model=GitOpsResourceTreeResponse,
)
async def get_gitops_resource_tree(
    kind: str,
    namespace: str,
    name: str,
    cluster_id: str = Query(min_length=1),
    api_version: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> GitOpsResourceTreeResponse:
    """Return one bounded controller tree from one inventory batch, never browser CR inference."""

    context = await _gitops_resource_context(
        db,
        current,
        cluster_id=cluster_id,
        api_version=api_version,
        kind=kind,
        namespace=namespace,
        name=name,
        write=False,
    )
    snapshot = await asyncio.to_thread(
        db.latest_inventory_snapshot,
        context["workspace_id"],
        context["cluster_id"],
    )
    try:
        return gitops_resource_tree(
            context["resource"],
            context["resources"],
            snapshot=snapshot,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    gateway_routes.GITOPS_RESOURCE_INSIGHTS_PATH,
    response_model=GitOpsResourceInsightsResponse,
)
async def get_gitops_resource_insights(
    kind: str,
    namespace: str,
    name: str,
    cluster_id: str = Query(min_length=1),
    api_version: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> GitOpsResourceInsightsResponse:
    """Return safe status/history facts with exact common action capabilities."""

    context = await _gitops_resource_context(
        db,
        current,
        cluster_id=cluster_id,
        api_version=api_version,
        kind=kind,
        namespace=namespace,
        name=name,
        write=False,
    )
    writable = _can_deploy(db, current, context["workspace_id"], context["cluster_id"])
    agent_available = await asyncio.to_thread(
        _gitops_agent_available,
        db,
        context["workspace_id"],
        context["cluster_id"],
    )
    try:
        insights = gitops_resource_insights(
            context["resource"],
            context["resources"],
            writable=writable,
            agent_available=agent_available,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return GitOpsResourceInsightsResponse(insights=insights)


@router.post(
    gateway_routes.GITOPS_RESOURCE_ACTION_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_gitops_resource_action(
    kind: str,
    namespace: str,
    name: str,
    payload: GitOpsResourceActionRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    """Queue one direct, RBAC-gated controller action through the canonical command stream."""

    api_version = (
        f"{payload.resource.api_group}/{payload.resource.version}"
        if payload.resource.api_group
        else payload.resource.version
    )
    context = await _gitops_resource_context(
        db,
        current,
        cluster_id=payload.cluster_id,
        api_version=api_version,
        kind=kind,
        namespace=namespace,
        name=name,
        write=True,
    )
    resource = context["resource"]
    _require_action_identity(payload, resource, kind=kind, namespace=namespace, name=name)
    if not await asyncio.to_thread(
        _gitops_agent_available,
        db,
        context["workspace_id"],
        context["cluster_id"],
    ):
        raise HTTPException(status_code=409, detail="GitOps control agent is unavailable")
    insights = gitops_resource_insights(
        resource,
        context["resources"],
        writable=True,
        agent_available=True,
    )
    if (
        payload.capability_revision != insights.capabilities.revision
        or payload.action not in insights.capabilities.actions
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": GITOPS_CAPABILITY_STALE,
                "detail": "GitOps resource capability changed; refresh before executing.",
            },
        )
    if payload.action == "sync" and payload.options and payload.options.resources:
        snapshot = await asyncio.to_thread(
            db.latest_inventory_snapshot,
            context["workspace_id"],
            context["cluster_id"],
        )
        _require_selective_sync_ownership(
            payload,
            gitops_resource_tree(resource, context["resources"], snapshot=snapshot),
        )

    source = gitops_source_observation(resource, context["resources"])
    command_payload: dict[str, Any] = {
        "action": payload.action,
        "requested_at": datetime.now(UTC).isoformat(),
        "resource_ref": payload.resource.model_dump(mode="json"),
        "resource_version": payload.resource_version,
    }
    if payload.options is not None:
        command_payload["sync_options"] = payload.options.model_dump(mode="json")
    if payload.refresh_mode is not None:
        command_payload["refresh_mode"] = payload.refresh_mode
    if payload.action == "sync_with_source":
        if source is None:
            raise HTTPException(status_code=409, detail="GitOps source observation is unavailable")
        source_ref = insights.source
        source_resource_version = str(source.get("resource_version") or "").strip()
        if source_ref is None or not source_resource_version:
            raise HTTPException(status_code=409, detail="GitOps source identity is incomplete")
        command_payload.update(
            {
                "source_ref": source_ref.model_dump(mode="json"),
                "source_resource_version": source_resource_version,
            }
        )

    request_fingerprint = _gitops_request_fingerprint(
        workspace_id=context["workspace_id"],
        payload=payload,
        command_payload=command_payload,
    )
    command_id = resource_action_command_id(
        context["workspace_id"],
        str(current.user_id),
        idempotency_key,
    )
    replay = await replay_resource_action_receipt(
        db,
        workspace_id=context["workspace_id"],
        command_id=command_id,
        request_fingerprint=request_fingerprint,
        idempotency_reused_code=GITOPS_IDEMPOTENCY_REUSED,
    )
    if replay is not None:
        return replay
    diff = Diff(
        workspace_id=context["workspace_id"],
        cluster_id=context["cluster_id"],
        resource=f"{payload.resource.kind}/{payload.resource.namespace}/{payload.resource.name}",
        namespace=payload.resource.namespace or "",
        desired_image="controller-action-requested",
        actual_image="controller-resource-observed",
        risk=RiskLevel.REVIEW_REQUIRED,
        status=payload.action,
        has_changes=True,
        changes=[
            {
                "operation": payload.action,
                "resource": payload.resource.model_dump(mode="json"),
            }
        ],
        basis={
            "request_fingerprint": request_fingerprint,
            "capability_revision": payload.capability_revision,
            "resource_version": payload.resource_version,
        },
    )
    command = CommandRequestedBody(
        cluster_id=context["cluster_id"],
        action=Command.GITOPS_RESOURCE_CONTROL_ACTION,
        namespace=payload.resource.namespace or "",
        reason=payload.reason,
        diff=diff,
        command_id=command_id,
        payload=command_payload,
        workspace_id=context["workspace_id"],
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(current.user_id),
        direct_execution=True,
        direct_execution_confirmed=True,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(str(current.user_id), tuple(current.roles)),
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=context["workspace_id"],
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


async def _gitops_resource_context(
    db: Any,
    current: Any,
    *,
    cluster_id: str,
    api_version: str,
    kind: str,
    namespace: str,
    name: str,
    write: bool,
) -> dict[str, Any]:
    workspace_id = str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID))
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
    resource, resources = await asyncio.gather(
        asyncio.to_thread(
            db.get_inventory_resource_by_api_version,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource_type=GITOPS_RESOURCE_TYPE,
            api_version=api_version,
            kind=kind,
            namespace=namespace,
            name=name,
        ),
        asyncio.to_thread(
            db.list_inventory_resources,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            include_deleted=False,
            limit=GITOPS_INVENTORY_QUERY_LIMIT,
        ),
    )
    if not isinstance(resource, Mapping):
        raise HTTPException(status_code=404, detail="GitOps resource not found")
    try:
        provider_for_inventory_resource(resource)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "resource": dict(resource),
        "resources": [dict(item) for item in resources if isinstance(item, Mapping)],
    }


def _gitops_agent_available(db: Any, workspace_id: str, cluster_id: str) -> bool:
    reader = getattr(db, "list_cluster_agent_statuses", None)
    if not callable(reader):
        return False
    required = {"command_receiver", Command.GITOPS_RESOURCE_CONTROL_CAPABILITY}
    return any(
        isinstance(status, Mapping)
        and cluster_connection_status(status) == AGENT_STATUS_ONLINE
        and required.issubset(set(status.get("capabilities") or ()))
        for status in reader(workspace_id, cluster_id)
    )


def _can_deploy(db: Any, current: Any, workspace_id: str, cluster_id: str) -> bool:
    try:
        require_cluster_access(
            db,
            current,
            workspace_id,
            cluster_id,
            Permission.DEPLOY_RUN.value,
        )
    except HTTPException:
        return False
    return True


def _require_action_identity(
    payload: GitOpsResourceActionRequest,
    resource: Mapping[str, Any],
    *,
    kind: str,
    namespace: str,
    name: str,
) -> None:
    if (
        payload.resource.kind.casefold() != kind.casefold()
        or payload.resource.namespace != namespace
        or payload.resource.name != name
        or payload.resource.uid != str(resource.get("uid") or "")
        or payload.resource_version != str(resource.get("resource_version") or "")
    ):
        raise HTTPException(status_code=409, detail="GitOps resource identity changed")


def _require_selective_sync_ownership(
    payload: GitOpsResourceActionRequest,
    tree: GitOpsResourceTreeResponse,
) -> None:
    """Reject stale or unowned Argo selective-sync targets before dispatch."""

    if tree.coverage.state != "complete":
        raise HTTPException(
            status_code=409,
            detail="selective sync requires a complete controller resource tree",
        )
    # ``tree.nodes`` are already restricted to controller-declared/owned
    # resources.  Excluding the root prevents using the selective path as a
    # second full-application sync shape.
    allowed = {
        (
            node.resource.api_group,
            node.resource.kind.casefold(),
            node.resource.namespace or "",
            node.resource.name,
        )
        for node in tree.nodes
        if node.resource.uid != tree.root.uid
    }
    for selected in payload.options.resources if payload.options else ():
        identity = (
            selected.api_group,
            selected.kind.casefold(),
            selected.namespace or "",
            selected.name,
        )
        if identity not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"selective sync resource is not owned by this controller: {selected.kind}/{selected.name}",
            )


def _gitops_request_fingerprint(
    *,
    workspace_id: str,
    payload: GitOpsResourceActionRequest,
    command_payload: Mapping[str, Any],
) -> str:
    value = {
        "workspace_id": workspace_id,
        "request": payload.model_dump(mode="json"),
        # requested_at is server-generated and excluded so retries are stable.
        "command": {key: item for key, item in command_payload.items() if key != "requested_at"},
    }
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _visible_runs(
    db: Any,
    *,
    workspace_id: str,
    application_id: str,
    allowed_cluster_ids: set[str],
) -> list[Mapping[str, Any]]:
    runs = await asyncio.to_thread(
        db.list_application_workflow_runs,
        workspace_id,
        application_id,
        limit=100,
    )
    return [
        run
        for run in runs
        if isinstance(run, Mapping) and str(run.get("cluster_id") or "") in allowed_cluster_ids
    ]


def _visible_bindings(
    bindings: object,
    allowed_cluster_ids: set[str],
) -> list[Mapping[str, Any]]:
    if not isinstance(bindings, list):
        return []
    return [
        binding
        for binding in bindings
        if isinstance(binding, Mapping)
        and str(binding.get("cluster_id") or "") in allowed_cluster_ids
    ]


def _actions_authorized(
    db: Any,
    current: Any,
    workspace_id: str,
    application_id: str,
    bindings: object,
) -> bool:
    manageable_applications = resolve_allowed_application_ids(
        db,
        current,
        workspace_id,
        Permission.APPLICATION_MANAGE.value,
    )
    if application_id not in manageable_applications:
        return False
    bound_cluster_ids = {
        str(binding.get("cluster_id") or "").strip()
        for binding in bindings
        if isinstance(binding, Mapping) and str(binding.get("cluster_id") or "").strip()
    }
    if not bound_cluster_ids:
        return False
    deployable_clusters = resolve_allowed_cluster_ids(
        db,
        current,
        workspace_id,
        Permission.DEPLOY_RUN.value,
    )
    return bound_cluster_ids.issubset(deployable_clusters)
