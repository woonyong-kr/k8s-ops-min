"""HTTP boundary for Helm release observations and capability-gated commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from domains.catalog.install import (
    CatalogHelmInstallPayload,
    CatalogHelmUpgradeGuard,
    CatalogInstallValidationError,
    CatalogRecipeUnsupported,
    ServerHelmRecipe,
    helm_upgrade_target,
    server_helm_recipe,
    server_helm_recipes,
    validate_catalog_values,
    validate_install_names,
)
from domains.catalog.router import (
    CATALOG_INSTALL_VALIDATION_ERROR,
    IDEMPOTENCY_KEY_PATTERN,
    canonical_hash,
    install_error,
)
from domains.command.events import CommandRequestedBody
from domains.command.repository import AgentCommandCapacityExceeded
from domains.command.router import (
    COMMAND_PRIORITY_HIGH,
    accept_command_with_receipt_stage,
    announce_staged_operation_event,
    command_accepted_response,
    new_command_id,
    publish_accepted_operation,
    replay_resource_action_receipt,
)
from domains.gitops.events import Diff
from domains.helm.release_projection import helm_release_detail, helm_release_list
from domains.helm.repository import HelmOwnedResourceObservationBatch
from domains.helm.source_provider import (
    HelmChartVersionProvider,
    compare_helm_chart_versions,
)
from domains.helm.source_router import get_helm_chart_version_provider
from domains.helm.upgrade_projection import (
    helm_release_upgrade_info,
    helm_release_version_list,
)
from domains.helm.upgrade_service import (
    helm_release_upgrade_key,
    resolve_helm_release_catalogs,
)
from domains.identity.dependencies import (
    require_cluster_access,
    require_session,
    resolve_allowed_cluster_ids,
)
from domains.target.connectivity import AGENT_STATUS_ONLINE, cluster_connection_status
from packages.config.constants import Command, RiskLevel, Sandbox
from packages.config.control import control_namespace_allowed
from packages.config.helm import helm_owned_resource_query_limit
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.helm import (
    HELM_ARTIFACT_MAX_ACTIVE_PER_CLUSTER,
    HELM_RELEASE_ARTIFACT_READ_ACTION,
    HELM_RELEASE_ARTIFACT_READ_CAPABILITY,
    HELM_RELEASE_OPERATION_ACTION,
    HELM_RELEASE_OPERATION_CAPABILITY,
    HELM_UPGRADE_BATCH_MAX_RELEASES,
    HELM_VALUES_PREVIEW_ACTION,
    HELM_VALUES_PREVIEW_CAPABILITY,
    HELM_VALUES_PREVIEW_MAX_ACTIVE_PER_CLUSTER,
    HelmArtifactCommandPayload,
    HelmArtifactReadRequest,
    HelmFeatureAvailability,
    HelmInstallTargetsResponse,
    HelmRelease,
    HelmReleaseCommands,
    HelmReleaseGuard,
    HelmReleaseInstallRequest,
    HelmReleaseOperationCommandPayload,
    HelmReleaseRollbackRequest,
    HelmReleaseUninstallRequest,
    HelmReleaseUpgradeBatch,
    HelmReleaseUpgradeInfo,
    HelmReleaseUpgradeRequest,
    HelmReleaseValuesPreviewRequest,
    HelmReleaseVersionList,
    HelmUpgradeTarget,
    HelmValuesPreviewCommandPayload,
)
from packages.contracts.helm.releases import HelmReleaseDetailResponse, HelmReleaseListResponse
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.parity import CommandReceipt
from packages.runtime.dependencies import get_db, get_events, get_operation_events

router = APIRouter()

INVALID_SCOPE_DETAIL = "Helm release scope is invalid"
SCOPE_NOT_FOUND_DETAIL = "Helm release scope not found"
RELEASE_NOT_FOUND_DETAIL = "Helm release not found"
ARTIFACT_AGENT_UNAVAILABLE_DETAIL = "Helm artifact reader is unavailable"
ARTIFACT_CAPACITY_DETAIL = "too many active Helm artifact reads"
UPGRADE_AGENT_UNAVAILABLE_DETAIL = "Helm release upgrade runner is unavailable"
UPGRADE_STALE_REVISION_DETAIL = "Helm release revision changed; refresh before upgrading"
UPGRADE_RECIPE_INVALID_DETAIL = "Helm release upgrade recipe is invalid"
UPGRADE_CHART_MISMATCH_DETAIL = "Helm release chart does not match the selected upgrade"
UPGRADE_SOURCE_TARGET_DETAIL = "Helm upgrade target is unavailable from the authorized source"
UPGRADE_NAMESPACE_UNAVAILABLE = "helm_upgrade_namespace_not_supported"
UPGRADE_PERMISSION_UNAVAILABLE = "helm_upgrade_permission_denied"
UPGRADE_AGENT_UNAVAILABLE = "helm_upgrade_agent_unavailable"
UPGRADE_REVISION_UNAVAILABLE = "helm_release_revision_unavailable"
UPGRADE_TARGETS_UNAVAILABLE = "helm_upgrade_targets_unavailable"
OPERATION_AGENT_UNAVAILABLE_DETAIL = "Helm release operation runner is unavailable"
OPERATION_STALE_REVISION_DETAIL = "Helm release revision changed; refresh before operating"
MAX_SCOPE_VALUES = 200


@dataclass(frozen=True)
class _ReviewedHelmCandidate:
    workspace_id: str
    cluster_id: str
    namespace: str
    release_name: str
    release: HelmRelease
    recipe: ServerHelmRecipe
    values: dict[str, Any]


@router.get(gateway_routes.HELM_RELEASES_PATH, response_model=HelmReleaseListResponse)
async def get_helm_releases(
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> HelmReleaseListResponse:
    """List only releases inferred from authorized inventory label metadata."""

    requested_clusters = _scope_values(clusters)
    requested_namespaces = _scope_values(namespaces)
    workspace_id = _workspace_id(current)
    allowed_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    _require_requested_clusters(requested_clusters, allowed_clusters)
    selected_clusters = requested_clusters or tuple(sorted(allowed_clusters))
    contexts, agent_statuses, storage_rows = await asyncio.gather(
        asyncio.to_thread(
            db.helm_release_observation_contexts,
            workspace_id=workspace_id,
            cluster_ids=selected_clusters,
        ),
        asyncio.to_thread(
            db.latest_cluster_agent_statuses,
            workspace_id,
            set(selected_clusters),
        ),
        asyncio.to_thread(
            db.list_helm_storage_observations,
            workspace_id=workspace_id,
            cluster_ids=selected_clusters,
            namespaces=requested_namespaces,
        ),
    )
    owned_resources = await _owned_resource_observations(
        db,
        workspace_id=workspace_id,
        storage_rows=storage_rows,
    )
    return helm_release_list(
        storage_rows,
        contexts=contexts,
        agent_statuses=agent_statuses,
        selected_cluster_ids=selected_clusters,
        owned_resource_rows=owned_resources.rows,
        owned_resources_truncated=owned_resources.truncated,
    )


@router.get(
    gateway_routes.HELM_RELEASE_PATH,
    response_model=HelmReleaseDetailResponse,
)
async def get_helm_release(
    namespace: str,
    release_name: str,
    cluster_id: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> HelmReleaseDetailResponse:
    """Read one exact authorized storage scope; no provider fallback is attempted."""

    selected_namespace = _single_scope_value(namespace)
    selected_release = _single_scope_value(release_name)
    selected_cluster = _single_scope_value(cluster_id)
    workspace_id = _workspace_id(current)
    allowed_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    _require_requested_clusters((selected_cluster,), allowed_clusters)
    contexts, agent_statuses, storage_rows, owned_resources, deploy_clusters = await asyncio.gather(
        asyncio.to_thread(
            db.helm_release_observation_contexts,
            workspace_id=workspace_id,
            cluster_ids=(selected_cluster,),
        ),
        asyncio.to_thread(
            db.latest_cluster_agent_statuses,
            workspace_id,
            {selected_cluster},
        ),
        asyncio.to_thread(
            db.list_helm_storage_observations,
            workspace_id=workspace_id,
            cluster_ids=(selected_cluster,),
            namespaces=(selected_namespace,),
        ),
        asyncio.to_thread(
            db.list_helm_owned_resource_observations,
            workspace_id=workspace_id,
            release_scopes=((selected_cluster, selected_namespace, selected_release),),
            limit=helm_owned_resource_query_limit(),
        ),
        asyncio.to_thread(
            resolve_allowed_cluster_ids,
            db,
            current,
            workspace_id,
            Permission.DEPLOY_RUN.value,
        ),
    )
    detail = helm_release_detail(
        storage_rows,
        contexts=contexts,
        agent_statuses=agent_statuses,
        selected_cluster_id=selected_cluster,
        namespace=selected_namespace,
        release_name=selected_release,
        owned_resource_rows=owned_resources.rows,
        owned_resources_truncated=owned_resources.truncated,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail=RELEASE_NOT_FOUND_DETAIL)
    commands = await asyncio.to_thread(
        _release_upgrade_commands,
        db,
        workspace_id,
        selected_cluster,
        selected_namespace,
        detail.detail.release.chart,
        detail.detail.release.chart_version,
        detail.detail.release.storage_resource_version,
        detail.detail.release.revision,
        selected_cluster in deploy_clusters,
    )
    return detail.model_copy(
        update={"detail": detail.detail.model_copy(update={"commands": commands})}
    )


@router.get(
    gateway_routes.HELM_RELEASE_UPGRADE_INFO_PATH,
    response_model=HelmReleaseUpgradeInfo,
)
async def get_helm_release_upgrade_info(
    namespace: str,
    release_name: str,
    cluster_id: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmReleaseUpgradeInfo:
    detail = await get_helm_release(namespace, release_name, cluster_id, current, db)
    release = detail.detail.release
    catalogs = await resolve_helm_release_catalogs(
        db=db,
        current=current,
        releases=(release,),
        provider=provider,
    )
    return helm_release_upgrade_info(
        chart_name=release.chart,
        current_version=release.chart_version,
        resolution=catalogs[helm_release_upgrade_key(release)],
    )


@router.get(
    gateway_routes.HELM_RELEASE_VERSIONS_PATH,
    response_model=HelmReleaseVersionList,
)
async def get_helm_release_versions(
    namespace: str,
    release_name: str,
    cluster_id: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmReleaseVersionList:
    detail = await get_helm_release(namespace, release_name, cluster_id, current, db)
    release = detail.detail.release
    catalogs = await resolve_helm_release_catalogs(
        db=db,
        current=current,
        releases=(release,),
        provider=provider,
    )
    return helm_release_version_list(
        chart_name=release.chart,
        current_version=release.chart_version,
        resolution=catalogs[helm_release_upgrade_key(release)],
    )


@router.get(
    gateway_routes.HELM_UPGRADE_CHECK_PATH,
    response_model=HelmReleaseUpgradeBatch,
)
async def get_helm_upgrade_check(
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmReleaseUpgradeBatch:
    release_list = await get_helm_releases(clusters, namespaces, current, db)
    selected = release_list.releases[:HELM_UPGRADE_BATCH_MAX_RELEASES]
    truncated = len(release_list.releases) > len(selected)
    catalogs = await resolve_helm_release_catalogs(
        db=db,
        current=current,
        releases=selected,
        provider=provider,
    )
    return HelmReleaseUpgradeBatch(
        releases={
            helm_release_upgrade_key(release): helm_release_upgrade_info(
                chart_name=release.chart,
                current_version=release.chart_version,
                resolution=catalogs[helm_release_upgrade_key(release)],
            )
            for release in selected
        },
        coverage=release_list.coverage,
        truncated=truncated,
        reason_codes=("helm_upgrade_batch_truncated",) if truncated else (),
        refresh_after_seconds=release_list.refresh_after_seconds,
    )


@router.post(
    gateway_routes.HELM_RELEASE_UPGRADE_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_helm_release_upgrade(
    namespace: str,
    release_name: str,
    payload: HelmReleaseUpgradeRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> CommandReceipt:
    """Upgrade one observed release through the existing digest-pinned agent executor."""

    candidate = await _reviewed_helm_candidate(
        namespace=namespace,
        release_name=release_name,
        cluster_id=payload.cluster_id,
        expected_revision=payload.expected_revision,
        catalog_item_id=payload.catalog_item_id,
        catalog_version=payload.catalog_version,
        submitted_values=payload.values,
        current=current,
        db=db,
        provider=provider,
        agent_supports=_agent_supports_release_upgrade,
    )
    release = candidate.release
    recipe = candidate.recipe

    command_payload = CatalogHelmInstallPayload(
        catalog_item_id=recipe.item_id,
        catalog_version=recipe.version,
        namespace=candidate.namespace,
        application_name=candidate.release_name,
        release_name=candidate.release_name,
        values=candidate.values,
        upgrade_guard=CatalogHelmUpgradeGuard(
            expected_revision=payload.expected_revision,
            storage=release.storage,
            storage_resource_version=release.storage_resource_version or "",
            chart_name=release.chart or "",
            chart_version=release.chart_version or "",
        ),
    )
    command = CommandRequestedBody(
        cluster_id=candidate.cluster_id,
        action=Command.CATALOG_HELM_INSTALL_ACTION,
        namespace=candidate.namespace,
        reason=payload.reason or f"upgrade Helm release {candidate.release_name}",
        diff=Diff(
            workspace_id=candidate.workspace_id,
            cluster_id=candidate.cluster_id,
            resource=f"helm-release/{candidate.namespace}/{candidate.release_name}",
            namespace=candidate.namespace,
            desired_image=f"catalog:{recipe.item_id}@{recipe.version}",
            actual_image=f"helm-revision:{release.revision}",
            risk=RiskLevel.SANDBOX_ONLY,
            status="upgrade",
            basis={
                "expected_revision": release.revision,
                "catalog_item_id": recipe.item_id,
                "catalog_version": recipe.version,
                "chart_version": recipe.chart_version,
            },
        ),
        command_id=new_command_id(),
        payload=command_payload.model_dump(mode="json"),
        workspace_id=candidate.workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(getattr(current, "user_id", "")),
        direct_execution=True,
        direct_execution_confirmed=True,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(
            str(getattr(current, "user_id", "")),
            tuple(getattr(current, "roles", ()) or ()),
        ),
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=candidate.workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


@router.post(
    gateway_routes.HELM_RELEASE_VALUES_PREVIEW_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_helm_values_preview(
    namespace: str,
    release_name: str,
    payload: HelmReleaseValuesPreviewRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> CommandReceipt:
    """Queue one bounded Agent-rendered preview for the exact observed release revision."""

    candidate = await _reviewed_helm_candidate(
        namespace=namespace,
        release_name=release_name,
        cluster_id=payload.cluster_id,
        expected_revision=payload.expected_revision,
        catalog_item_id=payload.catalog_item_id,
        catalog_version=payload.catalog_version,
        submitted_values=payload.values,
        current=current,
        db=db,
        provider=provider,
        agent_supports=_agent_supports_values_preview,
    )
    release = candidate.release
    command_payload = HelmValuesPreviewCommandPayload(
        namespace=candidate.namespace,
        release_name=candidate.release_name,
        catalog_item_id=candidate.recipe.item_id,
        catalog_version=candidate.recipe.version,
        values=candidate.values,
        guard=HelmReleaseGuard(
            expected_revision=payload.expected_revision,
            storage=release.storage,
            storage_resource_version=release.storage_resource_version or "",
            chart_name=release.chart or "",
            chart_version=release.chart_version or "",
        ),
    )
    command = CommandRequestedBody(
        cluster_id=candidate.cluster_id,
        action=HELM_VALUES_PREVIEW_ACTION,
        namespace=candidate.namespace,
        reason=f"preview Helm release {candidate.release_name} values",
        diff=Diff(
            workspace_id=candidate.workspace_id,
            cluster_id=candidate.cluster_id,
            resource=f"helm-release/{candidate.namespace}/{candidate.release_name}",
            namespace=candidate.namespace,
            desired_image=f"catalog:{candidate.recipe.item_id}@{candidate.recipe.version}",
            actual_image=f"helm-revision:{release.revision}",
            risk=RiskLevel.REVIEW_REQUIRED,
            status="preview",
            basis={
                "expected_revision": release.revision,
                "catalog_item_id": candidate.recipe.item_id,
                "catalog_version": candidate.recipe.version,
                "chart_version": candidate.recipe.chart_version,
            },
        ),
        command_id=new_command_id(),
        payload=command_payload.model_dump(mode="json"),
        workspace_id=candidate.workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(getattr(current, "user_id", "")),
        direct_execution=False,
        direct_execution_confirmed=False,
    )
    try:
        accepted, receipt_event = await accept_command_with_receipt_stage(
            events,
            command,
            actor=Actor(
                str(getattr(current, "user_id", "")),
                tuple(getattr(current, "roles", ()) or ()),
            ),
            max_active_per_action=HELM_VALUES_PREVIEW_MAX_ACTIVE_PER_CLUSTER,
        )
    except AgentCommandCapacityExceeded as error:
        raise HTTPException(
            status_code=429,
            detail="too many active Helm values previews",
            headers={"Retry-After": "1"},
        ) from error
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=candidate.workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


@router.get(
    gateway_routes.HELM_INSTALL_TARGETS_PATH,
    response_model=HelmInstallTargetsResponse,
)
async def list_helm_install_targets(
    current: Any = Depends(require_session),
) -> HelmInstallTargetsResponse:
    """List only digest-pinned recipes the target Agent can actually execute."""

    _ = current
    targets: list[HelmUpgradeTarget] = []
    for recipe in server_helm_recipes():
        target = helm_upgrade_target(recipe)
        if target is None:
            continue
        targets.append(target)
    return HelmInstallTargetsResponse(namespace=Sandbox.NAMESPACE, targets=tuple(targets))


@router.post(
    gateway_routes.HELM_RELEASE_INSTALL_STREAM_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_helm_release_install_stream(
    payload: HelmReleaseInstallRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    """Accept one digest-pinned install with a durable audited operation receipt."""

    workspace_id = _workspace_id(current)
    cluster_id = _single_scope_value(payload.cluster_id)
    namespace = _single_scope_value(payload.namespace)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.DEPLOY_RUN.value,
    )
    if namespace != Sandbox.NAMESPACE or not control_namespace_allowed(namespace):
        raise HTTPException(status_code=409, detail=UPGRADE_NAMESPACE_UNAVAILABLE)
    if not await asyncio.to_thread(_agent_supports_catalog_install, db, workspace_id, cluster_id):
        raise HTTPException(status_code=409, detail=UPGRADE_AGENT_UNAVAILABLE_DETAIL)
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise install_error(CATALOG_INSTALL_VALIDATION_ERROR, "invalid Idempotency-Key")
    try:
        recipe = server_helm_recipe(payload.catalog_item_id, payload.catalog_version)
        validate_install_names(
            application_name=payload.application_name,
            namespace=namespace,
            release_name=payload.release_name,
        )
        values = validate_catalog_values(recipe.values_schema, payload.values)
    except (CatalogRecipeUnsupported, CatalogInstallValidationError) as error:
        raise HTTPException(status_code=422, detail=UPGRADE_RECIPE_INVALID_DETAIL) from error
    command_payload = CatalogHelmInstallPayload(
        catalog_item_id=recipe.item_id,
        catalog_version=recipe.version,
        namespace=namespace,
        application_name=payload.application_name,
        release_name=payload.release_name,
        values=values,
    )
    request_fingerprint = canonical_hash(
        {
            "cluster_id": cluster_id,
            "payload": command_payload.model_dump(exclude_none=True),
            "recipe_digest": recipe.chart_digest,
            "recipe_fixed_values": recipe.fixed_values,
        }
    )
    identity = canonical_hash(
        {
            "idempotency_key": idempotency_key,
            "requested_by": str(getattr(current, "user_id", "")),
            "workspace_id": workspace_id,
        }
    )
    command_id = f"cmd-catalog-{identity[:24]}"
    replay = await replay_resource_action_receipt(
        db,
        workspace_id=workspace_id,
        command_id=command_id,
        request_fingerprint=request_fingerprint,
        idempotency_reused_code="helm_install_idempotency_key_reused",
    )
    if replay is not None:
        return replay
    command = CommandRequestedBody(
        cluster_id=cluster_id,
        action=Command.CATALOG_HELM_INSTALL_ACTION,
        namespace=namespace,
        reason=f"install Helm release {payload.release_name}",
        diff=Diff(
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            resource=f"helm-release/{namespace}/{payload.release_name}",
            namespace=namespace,
            desired_image=f"catalog:{recipe.item_id}@{recipe.version}",
            actual_image="release-not-installed",
            risk=RiskLevel.SANDBOX_ONLY,
            status="install",
            basis={
                "catalog_item_id": recipe.item_id,
                "catalog_version": recipe.version,
                "chart_version": recipe.chart_version,
                "request_fingerprint": request_fingerprint,
            },
        ),
        command_id=command_id,
        payload=command_payload.model_dump(mode="json"),
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(getattr(current, "user_id", "")),
        direct_execution=True,
        direct_execution_confirmed=True,
    )
    try:
        accepted, receipt_event = await accept_command_with_receipt_stage(
            events,
            command,
            actor=Actor(
                str(getattr(current, "user_id", "")),
                tuple(getattr(current, "roles", ()) or ()),
            ),
        )
    except AgentCommandCapacityExceeded as error:
        raise HTTPException(status_code=429, detail="Helm install capacity exceeded") from error
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


@router.post(
    gateway_routes.HELM_RELEASE_UPGRADE_STREAM_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
    include_in_schema=False,
)
async def create_helm_release_upgrade_stream(
    namespace: str,
    release_name: str,
    payload: HelmReleaseUpgradeRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> CommandReceipt:
    """Compatibility endpoint backed by the shared resumable operation stream."""

    return await create_helm_release_upgrade(
        namespace,
        release_name,
        payload,
        current,
        db,
        events,
        operation_events,
        provider,
    )


@router.put(
    gateway_routes.HELM_RELEASE_VALUES_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def apply_helm_release_values(
    namespace: str,
    release_name: str,
    payload: HelmReleaseUpgradeRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> CommandReceipt:
    """Apply reviewed values through the exact same revision-bound upgrade command."""

    return await create_helm_release_upgrade(
        namespace,
        release_name,
        payload,
        current,
        db,
        events,
        operation_events,
        provider,
    )


@router.post(
    gateway_routes.HELM_RELEASE_ROLLBACK_STREAM_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_helm_release_rollback(
    namespace: str,
    release_name: str,
    payload: HelmReleaseRollbackRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await _create_helm_release_operation(
        operation="rollback",
        namespace=namespace,
        release_name=release_name,
        cluster_id=payload.cluster_id,
        expected_revision=payload.expected_revision,
        rollback_revision=payload.revision,
        reason=payload.reason,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.delete(
    gateway_routes.HELM_RELEASE_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_helm_release_uninstall(
    namespace: str,
    release_name: str,
    payload: HelmReleaseUninstallRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await _create_helm_release_operation(
        operation="uninstall",
        namespace=namespace,
        release_name=release_name,
        cluster_id=payload.cluster_id,
        expected_revision=payload.expected_revision,
        rollback_revision=None,
        reason=payload.reason,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


async def _create_helm_release_operation(
    *,
    operation: str,
    namespace: str,
    release_name: str,
    cluster_id: str,
    expected_revision: int,
    rollback_revision: int | None,
    reason: str | None,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandReceipt:
    selected_namespace = _single_scope_value(namespace)
    selected_release = _single_scope_value(release_name)
    selected_cluster = _single_scope_value(cluster_id)
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        selected_cluster,
        Permission.DEPLOY_RUN.value,
    )
    if selected_namespace != Sandbox.NAMESPACE or not control_namespace_allowed(selected_namespace):
        raise HTTPException(status_code=409, detail=UPGRADE_NAMESPACE_UNAVAILABLE)
    if not await asyncio.to_thread(
        _agent_supports_release_operation,
        db,
        workspace_id,
        selected_cluster,
    ):
        raise HTTPException(status_code=409, detail=OPERATION_AGENT_UNAVAILABLE_DETAIL)
    detail = await get_helm_release(
        selected_namespace,
        selected_release,
        selected_cluster,
        current,
        db,
    )
    release = detail.detail.release
    if (
        release.revision != expected_revision
        or release.storage_resource_version is None
        or release.chart is None
        or release.chart_version is None
    ):
        raise HTTPException(status_code=409, detail=OPERATION_STALE_REVISION_DETAIL)
    command_payload = HelmReleaseOperationCommandPayload(
        operation=operation,
        namespace=selected_namespace,
        release_name=selected_release,
        guard=HelmReleaseGuard(
            expected_revision=expected_revision,
            storage=release.storage,
            storage_resource_version=release.storage_resource_version,
            chart_name=release.chart,
            chart_version=release.chart_version,
        ),
        rollback_revision=rollback_revision,
    )
    command = CommandRequestedBody(
        cluster_id=selected_cluster,
        action=HELM_RELEASE_OPERATION_ACTION,
        namespace=selected_namespace,
        reason=reason or f"{operation} Helm release {selected_release}",
        diff=Diff(
            workspace_id=workspace_id,
            cluster_id=selected_cluster,
            resource=f"helm-release/{selected_namespace}/{selected_release}",
            namespace=selected_namespace,
            desired_image=(
                f"helm-revision:{rollback_revision}"
                if rollback_revision is not None
                else "uninstalled"
            ),
            actual_image=f"helm-revision:{expected_revision}",
            risk=RiskLevel.SANDBOX_ONLY,
            status=operation,
            basis={
                "expected_revision": expected_revision,
                "rollback_revision": rollback_revision,
                "storage_uid": release.storage.uid,
                "storage_resource_version": release.storage_resource_version,
            },
        ),
        command_id=new_command_id(),
        payload=command_payload.model_dump(mode="json"),
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(getattr(current, "user_id", "")),
        direct_execution=True,
        direct_execution_confirmed=True,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(
            str(getattr(current, "user_id", "")),
            tuple(getattr(current, "roles", ()) or ()),
        ),
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


@router.post(
    gateway_routes.HELM_RELEASE_ARTIFACT_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def create_helm_artifact_read(
    namespace: str,
    release_name: str,
    payload: HelmArtifactReadRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    """Queue a revision-bound read; only the agent-sanitized artifact crosses the boundary."""

    selected_namespace = _single_scope_value(namespace)
    selected_release = _single_scope_value(release_name)
    selected_cluster = _single_scope_value(payload.cluster_id)
    workspace_id = _workspace_id(current)
    allowed_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    _require_requested_clusters((selected_cluster,), allowed_clusters)
    storage_rows = await asyncio.to_thread(
        db.list_helm_storage_observations,
        workspace_id=workspace_id,
        cluster_ids=(selected_cluster,),
        namespaces=(selected_namespace,),
    )
    if not _release_is_observed(storage_rows, selected_release):
        raise HTTPException(status_code=404, detail=RELEASE_NOT_FOUND_DETAIL)
    if not await asyncio.to_thread(
        _agent_supports_artifact_reads,
        db,
        workspace_id,
        selected_cluster,
    ):
        raise HTTPException(status_code=409, detail=ARTIFACT_AGENT_UNAVAILABLE_DETAIL)

    command_payload = HelmArtifactCommandPayload(
        **payload.model_dump(),
        namespace=selected_namespace,
        release_name=selected_release,
    )
    command = CommandRequestedBody(
        cluster_id=selected_cluster,
        action=HELM_RELEASE_ARTIFACT_READ_ACTION,
        namespace=selected_namespace,
        reason=f"read sanitized Helm {payload.artifact}",
        diff=Diff(
            workspace_id=workspace_id,
            cluster_id=selected_cluster,
            resource=f"helm-release/{selected_namespace}/{selected_release}",
            namespace=selected_namespace,
            desired_image="",
            actual_image="revision-observed",
            risk=RiskLevel.REVIEW_REQUIRED,
            status=HELM_RELEASE_ARTIFACT_READ_ACTION,
            basis={
                "artifact": payload.artifact,
                "revision": payload.revision,
                "comparison_revision": payload.comparison_revision,
                "all_values": payload.all_values,
            },
        ),
        command_id=new_command_id(),
        payload=command_payload.model_dump(mode="json"),
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=str(getattr(current, "user_id", "")),
        direct_execution=False,
        direct_execution_confirmed=False,
    )
    try:
        accepted, receipt_event = await accept_command_with_receipt_stage(
            events,
            command,
            actor=Actor(
                str(getattr(current, "user_id", "")),
                tuple(getattr(current, "roles", ()) or ()),
            ),
            max_active_per_action=HELM_ARTIFACT_MAX_ACTIVE_PER_CLUSTER,
        )
    except AgentCommandCapacityExceeded as error:
        raise HTTPException(
            status_code=429,
            detail=ARTIFACT_CAPACITY_DETAIL,
            headers={"Retry-After": "2"},
        ) from error
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events,
        receipt_event,
        workspace_id=workspace_id,
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


def _workspace_id(current: Any) -> str:
    return str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID) or DEFAULT_WORKSPACE_ID)


def _scope_values(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    values = tuple(sorted({part.strip() for part in value.split(",") if part.strip()}))
    if not values or len(values) > MAX_SCOPE_VALUES:
        raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL)
    return values


def _single_scope_value(value: str) -> str:
    values = _scope_values(value)
    if len(values) != 1:
        raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL)
    return values[0]


def _require_requested_clusters(requested: Iterable[str], allowed: set[str]) -> None:
    if not set(requested).issubset(allowed):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


async def _owned_resource_observations(
    db: Any,
    *,
    workspace_id: str,
    storage_rows: list[dict[str, Any]],
) -> HelmOwnedResourceObservationBatch:
    release_scopes: set[tuple[str, str, str]] = set()
    for row in storage_rows:
        cluster_id = str(row.get("cluster_id") or "").strip()
        namespace = str(row.get("namespace") or "").strip()
        labels = row.get("labels")
        release_name = str(labels.get("name") or "").strip() if isinstance(labels, dict) else ""
        if cluster_id and namespace and release_name:
            release_scopes.add((cluster_id, namespace, release_name))
    if not release_scopes:
        return HelmOwnedResourceObservationBatch(rows=(), truncated=False)
    return await asyncio.to_thread(
        db.list_helm_owned_resource_observations,
        workspace_id=workspace_id,
        release_scopes=tuple(sorted(release_scopes)),
        limit=helm_owned_resource_query_limit(),
    )


def _release_is_observed(rows: list[dict[str, Any]], release_name: str) -> bool:
    for row in rows:
        labels = row.get("labels")
        if isinstance(labels, Mapping) and str(labels.get("name") or "").strip() == release_name:
            return True
    return False


async def _reviewed_helm_candidate(
    *,
    namespace: str,
    release_name: str,
    cluster_id: str,
    expected_revision: int,
    catalog_item_id: str,
    catalog_version: str,
    submitted_values: Mapping[str, Any],
    current: Any,
    db: Any,
    provider: HelmChartVersionProvider,
    agent_supports: Callable[[Any, str, str], bool],
) -> _ReviewedHelmCandidate:
    """Resolve the same authorized, revision-bound candidate for preview and apply."""

    selected_namespace = _single_scope_value(namespace)
    selected_release = _single_scope_value(release_name)
    selected_cluster = _single_scope_value(cluster_id)
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        selected_cluster,
        Permission.DEPLOY_RUN.value,
    )
    if selected_namespace != Sandbox.NAMESPACE or not control_namespace_allowed(selected_namespace):
        raise HTTPException(status_code=409, detail=UPGRADE_NAMESPACE_UNAVAILABLE)
    if not await asyncio.to_thread(
        agent_supports,
        db,
        workspace_id,
        selected_cluster,
    ):
        raise HTTPException(status_code=409, detail=UPGRADE_AGENT_UNAVAILABLE_DETAIL)
    detail = await get_helm_release(
        selected_namespace,
        selected_release,
        selected_cluster,
        current,
        db,
    )
    release = detail.detail.release
    if (
        release.revision != expected_revision
        or release.storage_resource_version is None
        or release.chart is None
        or release.chart_version is None
    ):
        raise HTTPException(status_code=409, detail=UPGRADE_STALE_REVISION_DETAIL)
    try:
        recipe = server_helm_recipe(catalog_item_id, catalog_version)
        if not _recipe_matches_release(recipe, release.chart, release.chart_version):
            raise HTTPException(status_code=409, detail=UPGRADE_CHART_MISMATCH_DETAIL)
        validate_install_names(
            application_name=selected_release,
            namespace=selected_namespace,
            release_name=selected_release,
        )
        values = validate_catalog_values(recipe.values_schema, dict(submitted_values))
    except HTTPException:
        raise
    except (CatalogRecipeUnsupported, CatalogInstallValidationError) as error:
        raise HTTPException(status_code=422, detail=UPGRADE_RECIPE_INVALID_DETAIL) from error

    catalogs = await resolve_helm_release_catalogs(
        db=db,
        current=current,
        releases=(release,),
        provider=provider,
    )
    resolution = catalogs[helm_release_upgrade_key(release)]
    if (
        resolution.availability == "unavailable"
        or resolution.source is None
        or not any(
            not item.deprecated
            and compare_helm_chart_versions(item.version, recipe.chart_version) == 0
            for item in resolution.versions
        )
    ):
        raise HTTPException(status_code=409, detail=UPGRADE_SOURCE_TARGET_DETAIL)
    return _ReviewedHelmCandidate(
        workspace_id=workspace_id,
        cluster_id=selected_cluster,
        namespace=selected_namespace,
        release_name=selected_release,
        release=release,
        recipe=recipe,
        values=values,
    )


def _release_upgrade_commands(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
    chart_name: str | None,
    chart_version: str | None,
    storage_resource_version: str | None,
    revision: int | None,
    has_deploy_access: bool,
) -> HelmFeatureAvailability | HelmReleaseCommands:
    if not has_deploy_access:
        return HelmFeatureAvailability(reason_code=UPGRADE_PERMISSION_UNAVAILABLE)
    if namespace != Sandbox.NAMESPACE or not control_namespace_allowed(namespace):
        return HelmFeatureAvailability(reason_code=UPGRADE_NAMESPACE_UNAVAILABLE)
    if revision is None:
        return HelmFeatureAvailability(reason_code=UPGRADE_REVISION_UNAVAILABLE)
    if chart_name is None or chart_version is None or storage_resource_version is None:
        return HelmFeatureAvailability(reason_code=UPGRADE_TARGETS_UNAVAILABLE)
    if not _agent_supports_release_upgrade(db, workspace_id, cluster_id):
        return HelmFeatureAvailability(reason_code=UPGRADE_AGENT_UNAVAILABLE)
    targets = _helm_upgrade_targets(chart_name, chart_version)
    if not targets:
        return HelmFeatureAvailability(reason_code=UPGRADE_TARGETS_UNAVAILABLE)
    return HelmReleaseCommands(upgrade_targets=targets)


def _helm_upgrade_targets(
    chart_name: str,
    current_version: str,
) -> tuple[HelmUpgradeTarget, ...]:
    targets: list[HelmUpgradeTarget] = []
    for recipe in server_helm_recipes():
        if not _recipe_matches_release(recipe, chart_name, current_version):
            continue
        target = helm_upgrade_target(recipe)
        if target is None:
            continue
        targets.append(target)
    return tuple(targets)


def _recipe_matches_release(
    recipe: Any,
    chart_name: str | None,
    current_version: str | None,
) -> bool:
    return (
        chart_name is not None
        and current_version is not None
        and recipe.chart_name == chart_name
        and compare_helm_chart_versions(recipe.chart_version, current_version) > 0
    )


def _agent_supports_release_upgrade(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> bool:
    return _agent_supports_capabilities(
        db,
        workspace_id,
        cluster_id,
        Command.CATALOG_HELM_INSTALL_CAPABILITY,
        Command.CATALOG_HELM_UPGRADE_CAS_CAPABILITY,
        HELM_RELEASE_OPERATION_CAPABILITY,
    )


def _agent_supports_catalog_install(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> bool:
    return _agent_supports_capabilities(
        db,
        workspace_id,
        cluster_id,
        Command.CATALOG_HELM_INSTALL_CAPABILITY,
    )


def _agent_supports_values_preview(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> bool:
    return _agent_supports_capabilities(
        db,
        workspace_id,
        cluster_id,
        HELM_VALUES_PREVIEW_CAPABILITY,
    )


def _agent_supports_artifact_reads(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> bool:
    reader = getattr(db, "list_cluster_agent_statuses", None)
    if not callable(reader):
        return False
    statuses = reader(workspace_id, cluster_id)
    return any(
        isinstance(item, Mapping)
        and str(item.get("status") or "").casefold() == "connected"
        and HELM_RELEASE_ARTIFACT_READ_CAPABILITY in tuple(item.get("capabilities") or ())
        for item in statuses
    )


def _agent_supports_release_operation(
    db: Any,
    workspace_id: str,
    cluster_id: str,
) -> bool:
    return _agent_supports_capabilities(
        db,
        workspace_id,
        cluster_id,
        HELM_RELEASE_OPERATION_CAPABILITY,
    )


def _agent_supports_capabilities(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    *capabilities: str,
) -> bool:
    reader = getattr(db, "list_cluster_agent_statuses", None)
    if not callable(reader):
        return False
    required = {"command_receiver", *capabilities}
    return any(
        isinstance(item, Mapping)
        and cluster_connection_status(item) == AGENT_STATUS_ONLINE
        and required.issubset(set(item.get("capabilities") or ()))
        for item in reader(workspace_id, cluster_id)
    )
