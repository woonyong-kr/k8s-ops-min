"""Source-pinned manifest editing with Safe PR and audited direct apply choices."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from domains.command.events import CommandRequestedBody
from domains.command.router import (
    COMMAND_PRIORITY_HIGH,
    accept_command_with_receipt_stage,
    announce_staged_operation_event,
    command_accepted_response,
    publish_accepted_operation,
)
from domains.gitops.events import Diff
from domains.gitops.repository_discovery import (
    KUSTOMIZATION_FILES,
    ManifestRenderValidationError,
    RepositoryDiscoveryError,
    RepositoryDiscoveryService,
    collect_kustomize_source_contents,
    render_source_directory,
    source_type_from_path,
)
from domains.gitops.repository_discovery_router import (
    discovery_http_error,
    discovery_service,
    wizard_discovery_service,
)
from domains.identity.dependencies import (
    require_cluster_access,
    require_resource_access,
    require_session,
)
from domains.manifest_editor.source_revision import SourceRevision, SourceRevisionCodec
from domains.manifest_editor.validation import (
    MAX_DOCUMENTS,
    MAX_MANIFEST_BYTES,
    SERVER_OWNED_METADATA,
    ManifestIdentity,
    flattened_resources,
    manifest_identity,
    manifest_sha256,
    parse_documents,
    secret_safety_errors,
    validate_manifest_edit,
    validate_manifest_source,
)
from domains.scm.events import SafePrFilePatch, SafePrRequestedBody
from domains.scm.pipeline import safe_pr_patch_sha256
from packages.config.constants import Command, Sandbox
from packages.config.control import control_namespace_allowed
from packages.config.settings import env
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    ResourceManifestApproveRequest,
    ResourceManifestCreateDryRunRequest,
    ResourceManifestCreateRequest,
    ResourceManifestDirectApplyRequest,
    ResourceManifestPreviewRequest,
)
from packages.contracts.gateway.responses import (
    ResourceManifestApproveResponse,
    ResourceManifestCreateCapabilityResource,
    ResourceManifestCreateCapabilityResponse,
    ResourceManifestEditTarget,
    ResourceManifestImpact,
    ResourceManifestPreviewResponse,
    ResourceManifestSourceChoice,
    ResourceManifestSourceResponse,
)
from packages.contracts.gitops import SUPPORTED_KUBERNETES_RESOURCES, ApprovalStatus
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    AccessResourceType,
    Permission,
    ResourceRole,
)
from packages.contracts.kubernetes_discovery import ApiResourceDiscoveryObservation
from packages.contracts.parity import CommandReceipt, ResourceRef
from packages.runtime.dependencies import get_db, get_events, get_operation_events
from packages.storage.engine import unit_of_work_or_null

router = APIRouter()
SAFE_PR_MANIFEST_EDIT_KIND = "safe_pr_manifest_edit"
UNSUPPORTED_SOURCE = "Only a single raw YAML GitHub source can be edited safely."
STALE_SOURCE = "The manifest file changed after it was loaded. Reload before approving."
SOURCE_NOT_FOUND = "No exact GitOps source binding was found for this live resource."
SOURCE_PERMISSION_REQUIRED = "manifest_source_permission_required"
OWNER_SOURCE_NOT_FOUND = (
    "No exact Deployment, StatefulSet, or DaemonSet owner was observed for this Pod."
)
DIRECT_APPLY_AGENT_UNAVAILABLE = "agent_unavailable"
DIRECT_APPLY_UID_UNAVAILABLE = "resource_uid_unavailable"
DIRECT_APPLY_NAMESPACE_UNRESOLVED = "namespace_unresolved"
DIRECT_APPLY_NAMESPACE_DENIED = "namespace_not_allowed"
DIRECT_APPLY_UNAVAILABLE = "Direct manifest apply is unavailable for this exact target."
DIRECT_APPLY_PREVIEW_STALE = "The confirmed manifest differs from the validated preview."
SOURCE_REVISION_INVALID = "The resolved Git source expired or changed. Reload before approving."
SOURCE_REVISION_SIGNING_KEY_ENV = "FILTER_CURSOR_SIGNING_KEY"
IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
CREATE_CAPABILITY_UNAVAILABLE = "resource_create_capability_unavailable"
CREATE_DRY_RUN_REQUIRED = "resource_create_dry_run_required"
CREATE_FIELD_MANAGER = "opsia-resource-create"


@router.get(
    gateway_routes.RESOURCE_MANIFEST_SOURCE_PATH,
    response_model=ResourceManifestSourceResponse,
)
async def get_resource_manifest_source(
    resource_id: str,
    application_id: str | None = Query(default=None, max_length=200),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    fallback_service: RepositoryDiscoveryService = Depends(discovery_service),
) -> ResourceManifestSourceResponse:
    context = resource_context(db, current, resource_id, write=False)
    projection = manifest_source_projection(context)
    if context["edit_unavailable_reason"] is not None:
        return ResourceManifestSourceResponse(
            resource_id=resource_id,
            status="unsupported",
            choices=[],
            reason=str(context["edit_unavailable_reason"]),
            **projection,
        )
    sources, source_permission_denied = authorized_sources_with_access(
        db,
        current,
        context,
        application_id=application_id,
    )
    if not sources:
        return ResourceManifestSourceResponse(
            resource_id=resource_id,
            status="unsupported",
            choices=[],
            reason=(SOURCE_PERMISSION_REQUIRED if source_permission_denied else SOURCE_NOT_FOUND),
            **projection,
        )
    sources = [
        await resolve_bound_editable_source(
            db,
            current,
            source,
            context["identity"],
            fallback_service,
        )
        for source in sources
    ]
    choices = [source_choice(source) for source in sources]
    if application_id is None and len(sources) > 1:
        return ResourceManifestSourceResponse(
            resource_id=resource_id,
            status="ambiguous",
            choices=choices,
            reason="Choose the application source that owns this resource.",
            **projection,
        )
    source = sources[0]
    if not editable_source(source):
        return ResourceManifestSourceResponse(
            resource_id=resource_id,
            status="unsupported",
            choices=choices,
            selected=source_choice(source),
            reason=UNSUPPORTED_SOURCE,
            **projection,
        )
    base_sha, content = await read_pinned_source(db, current, source, fallback_service)
    safety_errors = validate_manifest_source(
        content,
        selected_identity=context["identity"],
    )
    if safety_errors:
        return ResourceManifestSourceResponse(
            resource_id=resource_id,
            status="unsupported",
            choices=choices,
            selected=source_choice(source),
            reason=safety_errors[0],
            **projection,
        )
    source_digest = manifest_sha256(content)
    return ResourceManifestSourceResponse(
        resource_id=resource_id,
        status="available",
        choices=choices,
        selected=source_choice(source),
        base_sha=base_sha,
        source_sha256=source_digest,
        source_revision_token=source_revision_token(
            current=current,
            context=context,
            source=source,
            resource_id=resource_id,
            base_sha=base_sha,
            source_sha256=source_digest,
        ),
        content=content,
        **projection,
    )


@router.post(
    gateway_routes.RESOURCE_MANIFEST_PREVIEW_PATH,
    response_model=ResourceManifestPreviewResponse,
)
async def preview_resource_manifest_edit(
    resource_id: str,
    payload: ResourceManifestPreviewRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    fallback_service: RepositoryDiscoveryService = Depends(discovery_service),
) -> ResourceManifestPreviewResponse:
    context = resource_context(db, current, resource_id, write=True)
    source = await exact_source(
        db,
        current,
        context,
        payload.application_id,
        fallback_service,
        resource_id=resource_id,
        base_sha=payload.base_sha,
        source_sha256=payload.source_sha256,
        source_revision_token=payload.source_revision_token,
    )
    base_sha, content = await read_pinned_source(db, current, source, fallback_service)
    ensure_source_is_current(payload.base_sha, payload.source_sha256, base_sha, content)
    validation = validate_manifest_edit(
        content,
        payload.edited_yaml,
        selected_identity=context["identity"],
    )
    impact = manifest_impacts(payload.edited_yaml, context["identity"]) if validation.valid else []
    reason_codes = direct_apply_reason_codes(db, context, impact)
    return ResourceManifestPreviewResponse(
        **asdict(validation),
        base_sha=base_sha,
        apply_availability="available" if not reason_codes else "unavailable",
        apply_reason_codes=reason_codes,
        impact=impact,
    )


@router.post(
    gateway_routes.RESOURCE_MANIFEST_APPLY_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def apply_resource_manifest_now(
    resource_id: str,
    payload: ResourceManifestDirectApplyRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
    fallback_service: RepositoryDiscoveryService = Depends(discovery_service),
) -> CommandReceipt:
    """Queue one confirmed, source-pinned manifest apply through the shared agent command path."""

    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise HTTPException(status_code=422, detail="invalid Idempotency-Key")
    context = resource_context(db, current, resource_id, write=True)
    source = await exact_source(
        db,
        current,
        context,
        payload.application_id,
        fallback_service,
        resource_id=resource_id,
        base_sha=payload.base_sha,
        source_sha256=payload.source_sha256,
        source_revision_token=payload.source_revision_token,
    )
    base_sha, content = await read_pinned_source(db, current, source, fallback_service)
    ensure_source_is_current(payload.base_sha, payload.source_sha256, base_sha, content)
    validation = validate_manifest_edit(
        content,
        payload.edited_yaml,
        selected_identity=context["identity"],
    )
    if not validation.valid:
        raise HTTPException(
            status_code=422,
            detail={"code": "manifest_invalid", "detail": validation.errors[0]},
        )
    if payload.expected_desired_sha256 != validation.desired_sha256:
        raise HTTPException(
            status_code=409,
            detail={"code": "manifest_preview_stale", "detail": DIRECT_APPLY_PREVIEW_STALE},
        )

    documents, impact = direct_apply_documents(payload.edited_yaml, context["identity"])
    reason_codes = direct_apply_reason_codes(db, context, impact)
    if reason_codes:
        policy_failure = any(
            reason in {"namespace_unresolved", "namespace_not_allowed"} for reason in reason_codes
        )
        raise HTTPException(
            status_code=422 if policy_failure else 409,
            detail={
                "code": "manifest_apply_unavailable",
                "detail": DIRECT_APPLY_UNAVAILABLE,
                "reason_codes": reason_codes,
            },
        )
    resource_ref = exact_resource_ref(context)
    request_fingerprint = manifest_apply_request_fingerprint(
        resource_id=resource_id,
        base_sha=base_sha,
        source_sha256=validation.source_sha256,
        desired_sha256=validation.desired_sha256,
        impact=impact,
    )
    command_id = manifest_apply_command_id(
        context["workspace_id"], str(current.user_id), idempotency_key
    )
    replay = await replay_manifest_apply_receipt(
        db,
        workspace_id=context["workspace_id"],
        command_id=command_id,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay

    selected_document = next(
        item for item, item_impact in zip(documents, impact, strict=True) if item_impact.selected
    )
    workflow_run_id = edit_workflow_id(
        context["workspace_id"],
        resource_id,
        source,
        base_sha,
        validation.desired_sha256,
    )
    diff = Diff(
        workspace_id=context["workspace_id"],
        cluster_id=context["cluster_id"],
        repository_id=str(source["repository_id"]),
        binding_id=str(source["binding_id"]),
        application_id=str(source["application_id"]),
        workflow_run_id=workflow_run_id,
        environment=str(source["environment"]),
        manifest_path=str(source["manifest_path"]),
        resource=f"{resource_ref.kind}/{resource_ref.name}",
        namespace=resource_ref.namespace or "",
        desired_image="",
        actual_image="resource-observed",
        risk=Sandbox.RISK_TAG,
        desired_manifest=selected_document,
        status="manifest_direct_apply",
        has_changes=True,
        changes=[item.model_dump() for item in impact],
        basis={
            "resource_ref": resource_ref.model_dump(),
            "base_sha": base_sha,
            "source_sha256": validation.source_sha256,
            "desired_sha256": validation.desired_sha256,
        },
    )
    command = CommandRequestedBody(
        cluster_id=context["cluster_id"],
        action=Command.APPLY_MANIFEST_ACTION,
        namespace=resource_ref.namespace or "",
        reason=payload.reason,
        diff=diff,
        command_id=command_id,
        payload={
            "request_fingerprint": request_fingerprint,
            "resource_ref": resource_ref.model_dump(),
            "source": {
                "repository_id": str(source["repository_id"]),
                "repo_ref": str(source["repo_ref"]),
                "branch": str(source["branch"]),
                "manifest_path": str(source["manifest_path"]),
                "base_sha": base_sha,
                "source_sha256": validation.source_sha256,
                "desired_sha256": validation.desired_sha256,
            },
            "desired_documents": documents,
        },
        workspace_id=context["workspace_id"],
        application_id=str(source["application_id"]),
        workflow_run_id=workflow_run_id,
        binding_id=str(source["binding_id"]),
        environment=str(source["environment"]),
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


@router.get(
    gateway_routes.RESOURCE_MANIFEST_CREATE_CAPABILITY_PATH,
    response_model=ResourceManifestCreateCapabilityResponse,
)
def get_resource_manifest_create_capability(
    cluster_id: str = Query(min_length=1, max_length=200),
    namespace: str = Query(min_length=1, max_length=253),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ResourceManifestCreateCapabilityResponse:
    """Project the exact server-observed create capability for one namespace."""

    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.DEPLOY_RUN.value,
    )
    snapshot_id, resources, reasons = manifest_create_capability(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
    )
    return ResourceManifestCreateCapabilityResponse(
        cluster_id=cluster_id,
        namespace=namespace,
        snapshot_id=snapshot_id,
        available=not reasons and bool(resources),
        reason_codes=reasons,
        max_documents=MAX_DOCUMENTS,
        max_bytes=MAX_MANIFEST_BYTES,
        resources=resources,
    )


@router.post(
    gateway_routes.RESOURCE_MANIFEST_CREATE_DRY_RUN_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def dry_run_resource_manifest_create(
    payload: ResourceManifestCreateDryRunRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    """Queue Kubernetes API server dry-run for a bounded create document set."""

    context = prepare_manifest_create_request(db, current, payload)
    return await dispatch_manifest_create_command(
        context=context,
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
        dry_run=True,
    )


@router.post(
    gateway_routes.RESOURCE_MANIFEST_CREATE_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def apply_resource_manifest_create(
    payload: ResourceManifestCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    """Create a server-dry-run-validated document set through the shared agent command."""

    context = prepare_manifest_create_request(db, current, payload)
    if payload.desired_sha256 != context["desired_sha256"]:
        raise HTTPException(
            status_code=409,
            detail={"code": "resource_create_stale", "detail": "YAML changed after dry-run."},
        )
    await require_successful_create_dry_run(
        db,
        workspace_id=context["workspace_id"],
        user_id=str(current.user_id),
        command_id=payload.dry_run_command_id,
        request_fingerprint=manifest_create_request_fingerprint(
            context,
            user_id=str(current.user_id),
            dry_run=True,
        ),
    )
    return await dispatch_manifest_create_command(
        context=context,
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
        dry_run=False,
    )


@router.post(
    gateway_routes.RESOURCE_MANIFEST_APPROVE_PATH,
    response_model=ResourceManifestApproveResponse,
)
async def approve_resource_manifest_edit(
    resource_id: str,
    payload: ResourceManifestApproveRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    fallback_service: RepositoryDiscoveryService = Depends(discovery_service),
) -> ResourceManifestApproveResponse:
    context = resource_context(db, current, resource_id, write=True)
    source = await exact_source(
        db,
        current,
        context,
        payload.application_id,
        fallback_service,
        resource_id=resource_id,
        base_sha=payload.base_sha,
        source_sha256=payload.source_sha256,
        source_revision_token=payload.source_revision_token,
    )
    base_sha, content = await read_pinned_source(db, current, source, fallback_service)
    ensure_source_is_current(payload.base_sha, payload.source_sha256, base_sha, content)
    validation = validate_manifest_edit(
        content,
        payload.edited_yaml,
        selected_identity=context["identity"],
    )
    if not validation.valid:
        raise HTTPException(
            status_code=422,
            detail={"code": "manifest_invalid", "detail": validation.errors[0]},
        )
    workflow_run_id = edit_workflow_id(
        context["workspace_id"], resource_id, source, base_sha, validation.desired_sha256
    )
    approval_id = f"approval-{workflow_run_id.removeprefix('workflow-')}"
    patch = SafePrFilePatch(
        path=str(source["manifest_path"]),
        content=payload.edited_yaml,
        description=f"Approved YAML edit for {context['identity'].kind}/{context['identity'].name}",
    )
    patch_sha256 = safe_pr_patch_sha256([patch])
    approval_details = {
        "authority": SAFE_PR_MANIFEST_EDIT_KIND,
        "resource_id": resource_id,
        "repository_id": str(source["repository_id"]),
        "repo_ref": str(source["repo_ref"]),
        "branch": str(source["branch"]),
        "manifest_path": str(source["manifest_path"]),
        "base_sha": base_sha,
        "source_sha256": validation.source_sha256,
        "desired_sha256": validation.desired_sha256,
        "patch_sha256": patch_sha256,
        "decision_reason": payload.reason,
    }
    request = SafePrRequestedBody(
        title=f"Update {context['identity'].kind} {context['identity'].name}",
        body=(
            f"Human-approved Opsia manifest edit. Reason: {payload.reason}\n\n"
            "The cluster remains unchanged until this Safe PR is reviewed and merged."
        ),
        provider="github",
        patches=[patch],
        pr_kind=SAFE_PR_MANIFEST_EDIT_KIND,
        workspace_id=context["workspace_id"],
        repository_id=str(source["repository_id"]),
        binding_id=str(source["binding_id"]),
        application_id=str(source["application_id"]),
        workflow_run_id=workflow_run_id,
        environment=str(source["environment"]),
        manifest_path=str(source["manifest_path"]),
        repo_ref=str(source["repo_ref"]),
        base_branch=str(source["branch"]),
        commit_sha=base_sha,
        patch_sha256=patch_sha256,
        approval_ref=approval_id,
        policy_decision_ref=f"manifest-editor:{approval_id}:granted",
    )
    with unit_of_work_or_null(db):
        db.request_workflow_approval(
            {
                "approval_id": approval_id,
                "workflow_run_id": workflow_run_id,
                "workspace_id": context["workspace_id"],
                "application_id": source["application_id"],
                "binding_id": source["binding_id"],
                "environment": source["environment"],
                "status": ApprovalStatus.GRANTED.value,
                "reason": payload.reason,
                "requested_role": ResourceRole.RELEASE_OPERATOR.value,
                "requested_by": current.user_id,
                "decided_by": current.user_id,
                "decision": "granted",
                "details": approval_details,
            }
        )
        accepted = await events.accept_body(
            request,
            actor=Actor(current.user_id, tuple(current.roles)),
        )
    return ResourceManifestApproveResponse(
        accepted=True,
        event_id=accepted.event.event_id,
        correlation_id=accepted.event.correlation_id,
        workflow_run_id=workflow_run_id,
        approval_id=approval_id,
    )


def manifest_impacts(
    desired_yaml: str,
    selected_identity: ManifestIdentity,
) -> list[ResourceManifestImpact]:
    errors, parsed = parse_documents(desired_yaml, label="edited")
    if errors or parsed is None:
        return []
    impacts: list[ResourceManifestImpact] = []
    for document in parsed:
        for resource in flattened_resources(document):
            identity = manifest_identity(resource)
            if identity is None:
                continue
            selected = identity_matches_selected(identity, selected_identity)
            namespace = identity.namespace
            if selected and namespace is None:
                namespace = selected_identity.namespace
            impacts.append(
                ResourceManifestImpact(
                    api_version=identity.api_version,
                    kind=identity.kind,
                    namespace=namespace,
                    name=identity.name,
                    selected=selected,
                )
            )
    return impacts


def manifest_create_capability(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
) -> tuple[str | None, list[ResourceManifestCreateCapabilityResource], list[str]]:
    reasons: list[str] = []
    if not control_namespace_allowed(namespace):
        reasons.append(DIRECT_APPLY_NAMESPACE_DENIED)
    statuses_reader = getattr(db, "list_cluster_agent_statuses", None)
    statuses = statuses_reader(workspace_id, cluster_id) if callable(statuses_reader) else []
    if not any(
        isinstance(item, Mapping)
        and str(item.get("status") or "").casefold() == "connected"
        and "command_receiver" in set(item.get("capabilities") or ())
        for item in statuses
    ):
        reasons.append(DIRECT_APPLY_AGENT_UNAVAILABLE)

    snapshot_reader = getattr(db, "latest_inventory_snapshot", None)
    snapshot = snapshot_reader(workspace_id, cluster_id) if callable(snapshot_reader) else None
    snapshot_id = (
        str(snapshot.get("snapshot_id") or "") or None if isinstance(snapshot, Mapping) else None
    )
    summary = snapshot.get("summary") if isinstance(snapshot, Mapping) else None
    nested_summary = summary.get("summary") if isinstance(summary, Mapping) else None
    raw_discovery = (
        nested_summary.get("api_resource_discovery")
        if isinstance(nested_summary, Mapping)
        else None
    )
    try:
        discovery = (
            ApiResourceDiscoveryObservation.model_validate(raw_discovery)
            if isinstance(raw_discovery, Mapping)
            else None
        )
    except ValueError:
        discovery = None
    if discovery is None:
        reasons.append("api_resource_discovery_unavailable")
        return snapshot_id, [], list(dict.fromkeys(reasons))
    if discovery.completeness != "exact":
        reasons.append("api_resource_discovery_incomplete")

    resources: list[ResourceManifestCreateCapabilityResource] = []
    for descriptor in discovery.resources:
        contract = SUPPORTED_KUBERNETES_RESOURCES.get((descriptor.api_version, descriptor.kind))
        verbs = set(descriptor.verbs)
        if (
            contract is None
            or not contract.namespaced
            or not descriptor.namespaced
            or contract.plural != descriptor.name
            or "create" not in verbs
        ):
            continue
        resources.append(
            ResourceManifestCreateCapabilityResource(
                api_version=descriptor.api_version,
                kind=descriptor.kind,
                resource=descriptor.name,
                force_supported="patch" in verbs,
            )
        )
    if not resources:
        reasons.append("no_allowlisted_create_resources")
    return snapshot_id, resources, list(dict.fromkeys(reasons))


def prepare_manifest_create_request(
    db: Any,
    current: Any,
    payload: ResourceManifestCreateDryRunRequest,
) -> dict[str, Any]:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        payload.cluster_id,
        Permission.INVENTORY_READ.value,
    )
    require_cluster_access(
        db,
        current,
        workspace_id,
        payload.cluster_id,
        Permission.DEPLOY_RUN.value,
    )
    snapshot_id, capability_resources, reasons = manifest_create_capability(
        db,
        workspace_id=workspace_id,
        cluster_id=payload.cluster_id,
        namespace=payload.namespace,
    )
    if reasons or snapshot_id != payload.snapshot_id:
        reason_codes = [*reasons]
        if snapshot_id != payload.snapshot_id:
            reason_codes.append("capability_snapshot_stale")
        raise HTTPException(
            status_code=409,
            detail={
                "code": CREATE_CAPABILITY_UNAVAILABLE,
                "detail": "Resource create capability is unavailable or stale.",
                "reason_codes": list(dict.fromkeys(reason_codes)),
            },
        )
    allowed = {(item.api_version, item.kind): item for item in capability_resources}
    documents, impact = prepare_manifest_create_documents(
        payload.edited_yaml,
        namespace=payload.namespace,
        allowed=allowed,
        force=payload.force,
    )
    desired_sha256 = manifest_documents_sha256(documents)
    return {
        "workspace_id": workspace_id,
        "cluster_id": payload.cluster_id,
        "namespace": payload.namespace,
        "snapshot_id": snapshot_id,
        "documents": documents,
        "impact": impact,
        "desired_sha256": desired_sha256,
        "force": payload.force,
        "reason": payload.reason,
    }


def prepare_manifest_create_documents(
    desired_yaml: str,
    *,
    namespace: str,
    allowed: Mapping[tuple[str, str], ResourceManifestCreateCapabilityResource],
    force: bool,
) -> tuple[list[dict[str, Any]], list[ResourceManifestImpact]]:
    errors, parsed = parse_documents(desired_yaml, label="create")
    if errors or parsed is None:
        raise HTTPException(status_code=422, detail=errors[0])
    safety_errors = secret_safety_errors(parsed)
    if safety_errors:
        raise HTTPException(status_code=422, detail=safety_errors[0])
    documents: list[dict[str, Any]] = []
    impact: list[ResourceManifestImpact] = []
    identities: set[tuple[str, str, str, str]] = set()
    for document in parsed:
        for resource in flattened_resources(document):
            identity = manifest_identity(resource)
            if identity is None:
                raise HTTPException(
                    status_code=422,
                    detail="every create document must have apiVersion, kind, and metadata.name",
                )
            if identity.namespace not in {None, namespace}:
                raise HTTPException(
                    status_code=422,
                    detail="every create document must use the selected namespace",
                )
            capability = allowed.get((identity.api_version, identity.kind))
            if capability is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"resource create is not allowlisted: {identity.api_version}/{identity.kind}",
                )
            if force and not capability.force_supported:
                raise HTTPException(
                    status_code=422,
                    detail=f"force create is not supported: {identity.api_version}/{identity.kind}",
                )
            normalized = sanitize_create_manifest(resource, namespace=namespace)
            key = (
                identity.api_version,
                identity.kind.casefold(),
                namespace,
                identity.name,
            )
            if key in identities:
                raise HTTPException(
                    status_code=422,
                    detail="create manifest contains duplicate resource identities",
                )
            identities.add(key)
            documents.append(normalized)
            impact.append(
                ResourceManifestImpact(
                    api_version=identity.api_version,
                    kind=identity.kind,
                    namespace=namespace,
                    name=identity.name,
                )
            )
    if not documents:
        raise HTTPException(status_code=422, detail="create manifest contains no resources")
    if len(documents) > MAX_DOCUMENTS:
        raise HTTPException(status_code=422, detail="create manifest exceeds document limit")
    return documents, impact


def sanitize_create_manifest(
    resource: Mapping[str, Any],
    *,
    namespace: str,
) -> dict[str, Any]:
    normalized = deepcopy(dict(resource))
    normalized.pop("status", None)
    metadata = normalized.get("metadata")
    if not isinstance(metadata, Mapping):
        raise HTTPException(status_code=422, detail="manifest metadata is invalid")
    clean_metadata = deepcopy(dict(metadata))
    for field in SERVER_OWNED_METADATA:
        clean_metadata.pop(field, None)
    clean_metadata.pop("generateName", None)
    annotations = clean_metadata.get("annotations")
    if isinstance(annotations, Mapping):
        clean_annotations = dict(annotations)
        clean_annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
        clean_metadata["annotations"] = clean_annotations
    clean_metadata["namespace"] = namespace
    normalized["metadata"] = clean_metadata
    return normalized


def manifest_documents_sha256(documents: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        documents,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def manifest_create_request_fingerprint(
    context: Mapping[str, Any],
    *,
    user_id: str,
    dry_run: bool,
) -> str:
    encoded = json.dumps(
        {
            "workspace_id": context["workspace_id"],
            "cluster_id": context["cluster_id"],
            "namespace": context["namespace"],
            "snapshot_id": context["snapshot_id"],
            "desired_sha256": context["desired_sha256"],
            "force": context["force"],
            "dry_run": dry_run,
            "requested_by": user_id,
            "reason": context["reason"],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


async def dispatch_manifest_create_command(
    *,
    context: Mapping[str, Any],
    payload: ResourceManifestCreateDryRunRequest,
    idempotency_key: str,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
    dry_run: bool,
) -> CommandReceipt:
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise HTTPException(status_code=422, detail="invalid Idempotency-Key")
    user_id = str(current.user_id)
    request_fingerprint = manifest_create_request_fingerprint(
        context,
        user_id=user_id,
        dry_run=dry_run,
    )
    command_id = manifest_apply_command_id(str(context["workspace_id"]), user_id, idempotency_key)
    replay = await replay_manifest_apply_receipt(
        db,
        workspace_id=str(context["workspace_id"]),
        command_id=command_id,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay
    impact = context["impact"]
    documents = context["documents"]
    workflow_run_id = f"workflow-resource-create-{request_fingerprint[:24]}"
    force = bool(context["force"])
    diff = Diff(
        workspace_id=str(context["workspace_id"]),
        cluster_id=str(context["cluster_id"]),
        workflow_run_id=workflow_run_id,
        environment=str(context["namespace"]),
        resource=",".join(f"{item.kind}/{item.name}" for item in impact),
        namespace=str(context["namespace"]),
        desired_image="",
        actual_image="unobserved",
        risk=Sandbox.RISK_TAG,
        desired_manifest=documents[0],
        status="manifest_create_dry_run" if dry_run else "manifest_create",
        has_changes=True,
        changes=[item.model_dump() for item in impact],
        basis={
            "capability_snapshot_id": context["snapshot_id"],
            "desired_sha256": context["desired_sha256"],
            "dry_run": dry_run,
            "force": force,
            "force_confirmation": bool(getattr(payload, "force_confirmation", False)),
        },
    )
    command = CommandRequestedBody(
        cluster_id=str(context["cluster_id"]),
        action=Command.APPLY_MANIFEST_ACTION,
        namespace=str(context["namespace"]),
        reason=payload.reason,
        diff=diff,
        command_id=command_id,
        payload={
            "request_fingerprint": request_fingerprint,
            "requested_by": user_id,
            "create_mode": True,
            "dry_run": dry_run,
            "force": force,
            "force_confirmation": bool(getattr(payload, "force_confirmation", False)),
            "field_manager": CREATE_FIELD_MANAGER,
            "capability_snapshot_id": context["snapshot_id"],
            "desired_sha256": context["desired_sha256"],
            "desired_documents": documents,
        },
        workspace_id=str(context["workspace_id"]),
        workflow_run_id=workflow_run_id,
        environment=str(context["namespace"]),
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=user_id,
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


async def require_successful_create_dry_run(
    db: Any,
    *,
    workspace_id: str,
    user_id: str,
    command_id: str,
    request_fingerprint: str,
) -> None:
    reader = getattr(db, "get_agent_command", None)
    existing = reader(command_id, workspace_id) if callable(reader) else None
    if inspect.isawaitable(existing):
        existing = await existing
    valid = isinstance(existing, Mapping)
    plan = existing.get("payload") if isinstance(existing, Mapping) else None
    command_payload = plan.get("payload") if isinstance(plan, Mapping) else None
    result = existing.get("result") if isinstance(existing, Mapping) else None
    valid = valid and isinstance(command_payload, Mapping) and isinstance(result, Mapping)
    valid = valid and command_payload.get("request_fingerprint") == request_fingerprint
    valid = valid and command_payload.get("requested_by") == user_id
    valid = valid and command_payload.get("create_mode") is True
    valid = valid and command_payload.get("dry_run") is True
    valid = valid and existing.get("action") == Command.APPLY_MANIFEST_ACTION
    valid = valid and str(existing.get("status") or "") == "completed"
    valid = valid and result.get("dry_run") is True
    valid = valid and result.get("completeness") == "exact"
    valid = valid and result.get("desired_sha256") == command_payload.get("desired_sha256")
    if not valid:
        raise HTTPException(
            status_code=409,
            detail={
                "code": CREATE_DRY_RUN_REQUIRED,
                "detail": "A matching successful Kubernetes server dry-run is required.",
            },
        )


def direct_apply_documents(
    desired_yaml: str,
    selected_identity: ManifestIdentity,
) -> tuple[list[dict[str, Any]], list[ResourceManifestImpact]]:
    errors, parsed = parse_documents(desired_yaml, label="edited")
    if errors or parsed is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "manifest_invalid", "detail": errors[0]},
        )
    documents: list[dict[str, Any]] = []
    impacts: list[ResourceManifestImpact] = []
    for document in parsed:
        for resource in flattened_resources(document):
            identity = manifest_identity(resource)
            if identity is None:
                raise HTTPException(status_code=422, detail="manifest identity is invalid")
            selected = identity_matches_selected(identity, selected_identity)
            normalized = deepcopy(dict(resource))
            namespace = identity.namespace
            if namespace is None and selected and selected_identity.namespace is not None:
                metadata = normalized.get("metadata")
                if not isinstance(metadata, dict):
                    raise HTTPException(status_code=422, detail="manifest metadata is invalid")
                metadata["namespace"] = selected_identity.namespace
                namespace = selected_identity.namespace
            documents.append(normalized)
            impacts.append(
                ResourceManifestImpact(
                    api_version=identity.api_version,
                    kind=identity.kind,
                    namespace=namespace,
                    name=identity.name,
                    selected=selected,
                )
            )
    return documents, impacts


def identity_matches_selected(identity: ManifestIdentity, selected: ManifestIdentity) -> bool:
    return (
        identity.api_version == selected.api_version
        and identity.kind.casefold() == selected.kind.casefold()
        and identity.name == selected.name
        and identity.namespace in {selected.namespace, None}
    )


def direct_apply_reason_codes(
    db: Any,
    context: dict[str, Any],
    impact: list[ResourceManifestImpact],
) -> list[str]:
    reasons: list[str] = []
    if not str(context["resource"].get("uid") or "").strip():
        reasons.append(DIRECT_APPLY_UID_UNAVAILABLE)
    statuses_reader = getattr(db, "list_cluster_agent_statuses", None)
    statuses = (
        statuses_reader(context["workspace_id"], context["cluster_id"])
        if callable(statuses_reader)
        else []
    )
    if not any(
        isinstance(item, Mapping)
        and str(item.get("status") or "").casefold() == "connected"
        and "command_receiver" in set(item.get("capabilities") or ())
        for item in statuses
    ):
        reasons.append(DIRECT_APPLY_AGENT_UNAVAILABLE)
    if any(item.namespace is None for item in impact):
        reasons.append(DIRECT_APPLY_NAMESPACE_UNRESOLVED)
    if any(
        item.namespace is not None and not control_namespace_allowed(item.namespace)
        for item in impact
    ):
        reasons.append(DIRECT_APPLY_NAMESPACE_DENIED)
    return list(dict.fromkeys(reasons))


def exact_resource_ref(context: dict[str, Any]) -> ResourceRef:
    resource = context["resource"]
    uid = str(resource.get("uid") or "").strip()
    if not uid:
        raise HTTPException(status_code=409, detail=DIRECT_APPLY_UNAVAILABLE)
    api_version = str(resource["api_version"])
    api_group, separator, version = api_version.rpartition("/")
    if not separator:
        api_group, version = "", api_version
    return ResourceRef(
        api_group=api_group,
        version=version,
        kind=str(resource["kind"]),
        namespace=(str(resource["namespace"]) if resource.get("namespace") is not None else None),
        name=str(resource["name"]),
        uid=uid,
    )


def manifest_apply_request_fingerprint(
    *,
    resource_id: str,
    base_sha: str,
    source_sha256: str,
    desired_sha256: str,
    impact: list[ResourceManifestImpact],
) -> str:
    encoded = json.dumps(
        {
            "resource_id": resource_id,
            "base_sha": base_sha,
            "source_sha256": source_sha256,
            "desired_sha256": desired_sha256,
            "impact": [item.model_dump() for item in impact],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def manifest_apply_command_id(workspace_id: str, user_id: str, idempotency_key: str) -> str:
    authority = "\0".join((workspace_id, user_id, idempotency_key))
    return f"cmd-manifest-{hashlib.sha256(authority.encode()).hexdigest()[:24]}"


async def replay_manifest_apply_receipt(
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
        raise HTTPException(
            status_code=409,
            detail={
                "code": IDEMPOTENCY_KEY_REUSED,
                "detail": "Idempotency-Key was already used for another manifest apply.",
            },
        )
    event_id = str(existing.get("confirmation_event_id") or "")
    correlation_id = str(existing.get("correlation_id") or "")
    if not event_id or not correlation_id:
        raise HTTPException(status_code=409, detail="manifest apply receipt is incomplete")
    return CommandReceipt(
        command_id=command_id,
        event_id=event_id,
        audit_event_id=event_id,
        correlation_id=correlation_id,
        status=str(existing.get("status") or "queued"),
    )


def resource_context(db: Any, current: Any, resource_id: str, *, write: bool) -> dict[str, Any]:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    resource = db.get_inventory_resource_by_key(
        workspace_id=workspace_id,
        inventory_key=resource_id,
    )
    if resource is None:
        raise HTTPException(status_code=404, detail="inventory resource not found")
    cluster_id = str(resource["cluster_id"])
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
    edit_resource = resource
    edit_relationship = "self"
    edit_unavailable_reason: str | None = None
    if str(resource.get("kind") or "").casefold() == "pod":
        resolver = getattr(db, "resolve_manifest_controller_owner", None)
        owner = (
            resolver(
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                resource=resource,
            )
            if callable(resolver)
            else None
        )
        if not isinstance(owner, Mapping):
            edit_unavailable_reason = OWNER_SOURCE_NOT_FOUND
        else:
            edit_resource = dict(owner)
            edit_relationship = "owner"
    return {
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "resource": edit_resource,
        "observed_resource": resource,
        "edit_relationship": edit_relationship,
        "edit_unavailable_reason": edit_unavailable_reason,
        "identity": ManifestIdentity(
            api_version=str(edit_resource["api_version"]),
            kind=str(edit_resource["kind"]),
            namespace=(
                str(edit_resource["namespace"])
                if edit_resource.get("namespace") is not None
                else None
            ),
            name=str(edit_resource["name"]),
        ),
    }


def authorized_sources(
    db: Any,
    current: Any,
    context: dict[str, Any],
    *,
    application_id: str | None,
) -> list[dict[str, Any]]:
    sources, _permission_denied = authorized_sources_with_access(
        db,
        current,
        context,
        application_id=application_id,
    )
    return sources


def authorized_sources_with_access(
    db: Any,
    current: Any,
    context: dict[str, Any],
    *,
    application_id: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    if context.get("edit_unavailable_reason") is not None:
        return [], False
    rows = db.list_resource_manifest_sources(
        workspace_id=context["workspace_id"],
        resource_id=str(context["resource"]["inventory_key"]),
        cluster_id=context["cluster_id"],
    )
    sources: list[dict[str, Any]] = []
    permission_denied = False
    for row in rows:
        source = dict(row)
        if application_id is not None and source.get("application_id") != application_id:
            continue
        try:
            require_resource_access(
                db,
                current,
                context["workspace_id"],
                AccessResourceType.APPLICATION.value,
                str(source["application_id"]),
                Permission.MANIFEST_READ.value,
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                permission_denied = True
                continue
            raise
        sources.append(source)
    return sources, permission_denied


async def exact_source(
    db: Any,
    current: Any,
    context: dict[str, Any],
    application_id: str,
    fallback: RepositoryDiscoveryService,
    *,
    resource_id: str,
    base_sha: str,
    source_sha256: str,
    source_revision_token: str | None,
) -> dict[str, Any]:
    if context.get("edit_unavailable_reason") is not None:
        raise HTTPException(status_code=409, detail=str(context["edit_unavailable_reason"]))
    sources = authorized_sources(db, current, context, application_id=application_id)
    if len(sources) != 1:
        raise HTTPException(status_code=409, detail=SOURCE_NOT_FOUND)
    bound_source = sources[0]
    require_resource_access(
        db,
        current,
        context["workspace_id"],
        AccessResourceType.APPLICATION.value,
        application_id,
        Permission.CONFIG_UPDATE.value,
    )
    source = source_from_revision_token(
        current=current,
        context=context,
        source=bound_source,
        application_id=application_id,
        resource_id=resource_id,
        base_sha=base_sha,
        source_sha256=source_sha256,
        token=source_revision_token,
    )
    if source is None:
        source = await resolve_bound_editable_source(
            db,
            current,
            bound_source,
            context["identity"],
            fallback,
        )
    if not editable_source(source):
        raise HTTPException(status_code=422, detail=UNSUPPORTED_SOURCE)
    return source


async def resolve_bound_editable_source(
    db: Any,
    current: Any,
    source: dict[str, Any],
    selected_identity: ManifestIdentity,
    fallback: RepositoryDiscoveryService,
) -> dict[str, Any]:
    if editable_source(source):
        return source
    inferred = str(source.get("source_type") or "") or source_type_from_path(
        str(source.get("manifest_path") or "")
    )
    if str(source.get("provider") or "") != "github" or inferred != "kustomize":
        return source
    try:
        service = await wizard_discovery_service(
            db,
            current,
            str(source["repo_ref"]),
            fallback,
        )
        return await resolve_kustomize_edit_source(source, selected_identity, service.client)
    except (ManifestRenderValidationError, RepositoryDiscoveryError, ValueError):
        # Source lookup remains fail-closed. The caller projects the existing
        # unsupported-source state instead of guessing a repository file.
        return source


async def resolve_kustomize_edit_source(
    source: dict[str, Any],
    selected_identity: ManifestIdentity,
    client: Any,
) -> dict[str, Any]:
    """Resolve one rendered Kustomize resource back to its exact Git YAML file.

    Only files reachable through the bounded local Kustomize reference graph
    are considered. A missing or ambiguous identity remains unsupported rather
    than allowing an edit against a guessed file.
    """

    repo_ref = str(source["repo_ref"])
    branch = str(source["branch"])
    base_sha = await client.branch_sha(repo_ref, branch)
    tree, _warnings = await client.tree_at_revision(repo_ref, base_sha)
    source_dir = render_source_directory(str(source["manifest_path"]), "kustomize")
    contents, _dependency_warnings = await collect_kustomize_source_contents(
        client,
        repo_ref,
        base_sha,
        source_dir,
        tree,
    )
    matches: list[str] = []
    for path, raw in sorted(contents.items()):
        if path.rsplit("/", 1)[-1] in KUSTOMIZATION_FILES:
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
        if any(
            identity is not None and identity_matches_selected(identity, selected_identity)
            for document in documents
            for resource in flattened_resources(document)
            for identity in [manifest_identity(resource)]
        ):
            matches.append(path)
    if len(matches) != 1:
        return source
    return {
        **source,
        "binding_manifest_path": str(source["manifest_path"]),
        "manifest_path": matches[0],
        "source_type": "raw-yaml",
        "render_source_type": "kustomize",
    }


def editable_source(source: dict[str, Any]) -> bool:
    source_type = str(source.get("source_type") or "")
    inferred = source_type or source_type_from_path(str(source.get("manifest_path") or ""))
    return (
        str(source.get("provider") or "") == "github"
        and inferred == "raw-yaml"
        and str(source.get("manifest_path") or "").lower().endswith((".yaml", ".yml"))
    )


def source_revision_codec() -> SourceRevisionCodec:
    return SourceRevisionCodec(env(SOURCE_REVISION_SIGNING_KEY_ENV, "").strip())


def source_revision_scope(
    *,
    current: Any,
    context: Mapping[str, Any],
    source: Mapping[str, Any],
    application_id: str,
    resource_id: str,
    base_sha: str,
    source_sha256: str,
) -> SourceRevision:
    return SourceRevision(
        workspace_id=str(context["workspace_id"]),
        user_id=str(current.user_id),
        resource_id=resource_id,
        application_id=application_id,
        repository_ref=str(source["repo_ref"]),
        branch=str(source["branch"]),
        binding_manifest_path=str(
            source.get("binding_manifest_path") or source["manifest_path"]
        ),
        resolved_manifest_path=str(source["manifest_path"]),
        base_sha=base_sha,
        source_sha256=source_sha256,
    )


def source_revision_token(
    *,
    current: Any,
    context: Mapping[str, Any],
    source: Mapping[str, Any],
    resource_id: str,
    base_sha: str,
    source_sha256: str,
) -> str:
    return source_revision_codec().encode(
        source_revision_scope(
            current=current,
            context=context,
            source=source,
            application_id=str(source["application_id"]),
            resource_id=resource_id,
            base_sha=base_sha,
            source_sha256=source_sha256,
        )
    )


def source_from_revision_token(
    *,
    current: Any,
    context: Mapping[str, Any],
    source: Mapping[str, Any],
    application_id: str,
    resource_id: str,
    base_sha: str,
    source_sha256: str,
    token: str | None,
) -> dict[str, Any] | None:
    if token is None:
        return None
    try:
        codec = source_revision_codec()
        decoded = codec.inspect(token)
        expected = source_revision_scope(
            current=current,
            context=context,
            source={
                **source,
                "binding_manifest_path": str(source["manifest_path"]),
                "manifest_path": decoded.resolved_manifest_path,
            },
            application_id=application_id,
            resource_id=resource_id,
            base_sha=base_sha,
            source_sha256=source_sha256,
        )
        codec.decode(token, expected=expected)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "manifest_source_revision_invalid",
                "detail": SOURCE_REVISION_INVALID,
            },
        ) from exc
    return {
        **source,
        "binding_manifest_path": decoded.binding_manifest_path,
        "manifest_path": decoded.resolved_manifest_path,
        "source_type": "raw-yaml",
    }


def source_choice(source: dict[str, Any]) -> ResourceManifestSourceChoice:
    return ResourceManifestSourceChoice(
        application_id=str(source["application_id"]),
        application_name=str(source["application_name"]),
        repository_ref=str(source["repo_ref"]),
        branch=str(source["branch"]),
        manifest_path=str(source["manifest_path"]),
        environment=str(source["environment"]),
    )


def manifest_source_projection(context: Mapping[str, Any]) -> dict[str, Any]:
    observed = context["observed_resource"]
    raw = observed.get("raw")
    live_yaml: str | None = None
    live_reason: str | None = None
    if not isinstance(raw, Mapping):
        live_reason = "The retained live inventory snapshot does not include this manifest."
    else:
        document = deepcopy(dict(raw))
        safety_errors = secret_safety_errors([document])
        if safety_errors:
            live_reason = "The live manifest contains sensitive inline values and cannot be shown."
        else:
            live_yaml = yaml.safe_dump(
                document,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
    observed_at = observed.get("observed_at")
    if observed_at is not None and hasattr(observed_at, "isoformat"):
        observed_at = observed_at.isoformat()
    elif observed_at is not None:
        observed_at = str(observed_at)
    edit_target = None
    if context.get("edit_unavailable_reason") is None:
        resource = context["resource"]
        edit_target = ResourceManifestEditTarget(
            resource_id=str(resource["inventory_key"]),
            relationship=str(context["edit_relationship"]),
            kind=str(resource["kind"]),
            namespace=(
                str(resource["namespace"]) if resource.get("namespace") is not None else None
            ),
            name=str(resource["name"]),
        )
    return {
        "live_yaml": live_yaml,
        "live_observed_at": observed_at,
        "live_reason": live_reason,
        "edit_target": edit_target,
    }


async def read_pinned_source(
    db: Any,
    current: Any,
    source: dict[str, Any],
    fallback: RepositoryDiscoveryService,
) -> tuple[str, str]:
    try:
        service = await wizard_discovery_service(
            db,
            current,
            str(source["repo_ref"]),
            fallback,
        )
        base_sha = await service.client.branch_sha(str(source["repo_ref"]), str(source["branch"]))
        content = await service.client.content(
            str(source["repo_ref"]), base_sha, str(source["manifest_path"])
        )
        return base_sha, content.decode("utf-8")
    except (RepositoryDiscoveryError, ValueError, UnicodeDecodeError) as exc:
        raise discovery_http_error(exc) from exc


def ensure_source_is_current(
    _approved_base_sha: str,
    approved_source_sha256: str,
    _current_base_sha: str,
    current_source: str,
) -> None:
    # A branch can advance because an unrelated file changed while this editor
    # is open. The approval is scoped to the resolved manifest path and its
    # content digest, so only a change to that file makes the edit stale. The
    # current branch SHA is still used downstream when the Safe PR is created.
    if approved_source_sha256 != manifest_sha256(current_source):
        raise HTTPException(
            status_code=409,
            detail={"code": "manifest_source_stale", "detail": STALE_SOURCE},
        )


def edit_workflow_id(
    workspace_id: str,
    resource_id: str,
    source: dict[str, Any],
    base_sha: str,
    desired_sha256: str,
) -> str:
    authority = "\0".join(
        (
            workspace_id,
            resource_id,
            str(source["application_id"]),
            str(source["binding_id"]),
            base_sha,
            desired_sha256,
        )
    )
    return f"workflow-manifest-edit-{hashlib.sha256(authority.encode()).hexdigest()[:32]}"
