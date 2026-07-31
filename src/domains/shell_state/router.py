"""Session/RBAC guarded namespace-scope and UI preference APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from domains.identity.dependencies import require_cluster_access, require_session
from domains.resource_access.projection import (
    ResourceAccessUnavailable,
    agent_execution_access_projection,
)
from domains.shell_state.events import NamespaceScopeUpdatedBody, UiPreferencesUpdatedBody
from domains.shell_state.node_alias_router import router as node_alias_router
from packages.config.refresh_policies import browser_refresh_policies
from packages.contracts.auth import Actor
from packages.contracts.freshness import BrowserRefreshPoliciesResponse
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    AccessResourceType,
    Permission,
)
from packages.contracts.shell_state import (
    NamespaceScopeResponse,
    NamespaceScopeUpdateRequest,
    NamespaceScopeUpdateResponse,
    SettingsAccessDecision,
    SettingsAccessProfileResponse,
    SettingsObservedKubernetesRules,
    SettingsObservedRestrictedResourceTypes,
    SettingsUnavailableEvidence,
    ShellFreshness,
    UiPreferences,
    UiPreferencesResponse,
    UiPreferencesUpdateRequest,
    UiPreferencesUpdateResponse,
)
from packages.runtime.dependencies import get_db, get_events

router = APIRouter()
router.include_router(node_alias_router)
NAMESPACE_CATALOG_LIMIT = 1000
CONFLICT_DETAIL = "shell state revision conflict"
INVALID_NAMESPACE_DETAIL = "namespace scope contains inaccessible namespaces"
SCOPE_UNAVAILABLE_DETAIL = "namespace catalog is unavailable"
SETTINGS_ACCESS_UNAVAILABLE_DETAIL = "effective access repository is unavailable"


@router.get(
    gateway_routes.REFRESH_POLICIES_PATH,
    response_model=BrowserRefreshPoliciesResponse,
)
async def get_refresh_policies(
    _current: Any = Depends(require_session),
) -> BrowserRefreshPoliciesResponse:
    """Return the validated refresh inventory without browser-owned defaults."""

    return browser_refresh_policies()


@router.get(
    gateway_routes.CLUSTER_NAMESPACE_SCOPE_PATH,
    response_model=NamespaceScopeResponse,
)
async def get_namespace_scope(
    cluster_id: str = Query(min_length=1, max_length=255),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> NamespaceScopeResponse:
    workspace_id = _workspace_id(current)
    _require_inventory_read(db, current, workspace_id, cluster_id)
    context, persisted = await asyncio.gather(
        _snapshot_context(db, workspace_id, cluster_id),
        asyncio.to_thread(
            db.get_namespace_scope,
            workspace_id=workspace_id,
            user_id=current.user_id,
            cluster_id=cluster_id,
        ),
    )
    catalog = await _namespace_catalog(db, workspace_id, cluster_id, context)
    authorized_actives = await _resolve_persisted_namespaces(
        db,
        workspace_id,
        cluster_id,
        context,
        persisted,
    )
    return _namespace_response(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        context=context,
        catalog=catalog,
        persisted=persisted,
        authorized_actives=authorized_actives,
    )


@router.post(
    gateway_routes.CLUSTER_NAMESPACE_PATH,
    response_model=NamespaceScopeUpdateResponse,
)
async def update_namespace_scope(
    payload: NamespaceScopeUpdateRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> NamespaceScopeUpdateResponse:
    workspace_id = _workspace_id(current)
    _require_inventory_read(db, current, workspace_id, payload.cluster_id)
    context = await _snapshot_context(db, workspace_id, payload.cluster_id)
    snapshot_revision = int(context.get("snapshot_revision") or 0)
    if snapshot_revision <= 0:
        raise HTTPException(status_code=409, detail=SCOPE_UNAVAILABLE_DETAIL)
    resolved = await asyncio.to_thread(
        db.resolve_authorized_namespaces,
        workspace_id=workspace_id,
        cluster_id=payload.cluster_id,
        snapshot_revision=snapshot_revision,
        namespaces=payload.namespaces,
    )
    if resolved != set(payload.namespaces):
        raise HTTPException(status_code=403, detail=INVALID_NAMESPACE_DETAIL)

    persisted: dict[str, Any] | None = None

    def stage(conn: Any, _event: Any) -> None:
        nonlocal persisted
        persisted = db.put_namespace_scope(
            workspace_id=workspace_id,
            user_id=current.user_id,
            cluster_id=payload.cluster_id,
            namespaces=payload.namespaces,
            expected_revision=payload.expected_revision,
            conn=conn,
        )
        if persisted is None:
            raise ShellStateRevisionConflict

    try:
        accepted = await events.accept_body(
            NamespaceScopeUpdatedBody(
                workspace_id=workspace_id,
                user_id=current.user_id,
                cluster_id=payload.cluster_id,
                namespaces=list(payload.namespaces),
                expected_revision=payload.expected_revision,
            ),
            actor=Actor(current.user_id, tuple(current.roles)),
            transactional_stage=stage,
        )
    except ShellStateRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=CONFLICT_DETAIL) from exc
    if persisted is None:
        raise RuntimeError("namespace scope transaction did not persist state")
    catalog = await _namespace_catalog(db, workspace_id, payload.cluster_id, context)
    response = _namespace_response(
        workspace_id=workspace_id,
        cluster_id=payload.cluster_id,
        context=context,
        catalog=catalog,
        persisted=persisted,
        authorized_actives=resolved,
    )
    return NamespaceScopeUpdateResponse(
        **response.model_dump(),
        event_id=accepted.event.event_id,
        audit_event_id=accepted.event.event_id,
    )


@router.get(gateway_routes.SETTINGS_PATH, response_model=UiPreferencesResponse)
async def get_settings(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> UiPreferencesResponse:
    workspace_id = _workspace_id(current)
    persisted = await asyncio.to_thread(
        db.get_ui_preferences,
        workspace_id=workspace_id,
        user_id=current.user_id,
    )
    if persisted is None:
        return UiPreferencesResponse(
            workspace_id=workspace_id,
            user_id=current.user_id,
            preferences=UiPreferences(),
            revision=0,
        )
    return UiPreferencesResponse.model_validate(persisted)


@router.put(gateway_routes.SETTINGS_PATH, response_model=UiPreferencesUpdateResponse)
async def update_settings(
    payload: UiPreferencesUpdateRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> UiPreferencesUpdateResponse:
    workspace_id = _workspace_id(current)
    persisted: dict[str, Any] | None = None
    preferences = payload.preferences.model_dump()

    def stage(conn: Any, _event: Any) -> None:
        nonlocal persisted
        persisted = db.put_ui_preferences(
            workspace_id=workspace_id,
            user_id=current.user_id,
            preferences=preferences,
            expected_revision=payload.expected_revision,
            conn=conn,
        )
        if persisted is None:
            raise ShellStateRevisionConflict

    try:
        accepted = await events.accept_body(
            UiPreferencesUpdatedBody(
                workspace_id=workspace_id,
                user_id=current.user_id,
                preferences=preferences,
                expected_revision=payload.expected_revision,
            ),
            actor=Actor(current.user_id, tuple(current.roles)),
            transactional_stage=stage,
        )
    except ShellStateRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=CONFLICT_DETAIL) from exc
    if persisted is None:
        raise RuntimeError("UI preference transaction did not persist state")
    return UiPreferencesUpdateResponse(
        **UiPreferencesResponse.model_validate(persisted).model_dump(),
        event_id=accepted.event.event_id,
        audit_event_id=accepted.event.event_id,
    )


@router.get(
    gateway_routes.SETTINGS_ACCESS_PATH,
    response_model=SettingsAccessProfileResponse,
)
async def get_settings_access(
    cluster_id: str = Query(min_length=1, max_length=255),
    namespace: str = Query(default="default", min_length=1, max_length=253),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> SettingsAccessProfileResponse:
    """Return product RBAC beside the observed cluster-agent execution authority."""
    workspace_id = _workspace_id(current)
    _require_inventory_read(db, current, workspace_id, cluster_id)
    resolver = getattr(db, "effective_permissions_for_resource", None)
    if not callable(resolver):
        raise HTTPException(status_code=503, detail=SETTINGS_ACCESS_UNAVAILABLE_DETAIL)
    allowed = await asyncio.to_thread(
        resolver,
        current.user_id,
        workspace_id,
        AccessResourceType.CLUSTER.value,
        cluster_id,
    )
    allowed_permissions = {
        str(permission)
        for permission in allowed
        if str(permission) in {candidate.value for candidate in Permission}
    }
    decisions = tuple(
        SettingsAccessDecision(
            permission=permission.value,
            category=permission.value.partition(".")[0],
            allowed=permission.value in allowed_permissions,
        )
        for permission in sorted(Permission, key=lambda item: item.value)
    )
    roles = tuple(sorted({str(role) for role in (getattr(current, "roles", ()) or ())}))
    snapshot_reader = getattr(db, "latest_inventory_snapshot", None)
    execution_access = None
    if callable(snapshot_reader):
        snapshot = await asyncio.to_thread(snapshot_reader, workspace_id, cluster_id)
        if isinstance(snapshot, dict):
            try:
                execution_access = agent_execution_access_projection(
                    snapshot,
                    namespace=namespace,
                )
            except (ResourceAccessUnavailable, ValueError):
                execution_access = None
    if execution_access is None:
        kubernetes_rules = SettingsUnavailableEvidence(
            reason_code="agent_access_evidence_unavailable",
            detail=(
                "The cluster agent has not produced a complete execution-authority "
                "observation for this namespace."
            ),
        )
        restricted_resource_types = SettingsUnavailableEvidence(
            reason_code="agent_discovery_evidence_unavailable",
            detail=(
                "The cluster agent has not produced enough RBAC and discovery evidence "
                "to classify restricted resource types."
            ),
        )
    else:
        kubernetes_rules = SettingsObservedKubernetesRules(
            namespace=execution_access.namespace,
            observed_at=execution_access.observed_at,
            subject=execution_access.subject,
            resource_rules=execution_access.resource_rules,
            non_resource_rules=execution_access.non_resource_rules,
            truncated=execution_access.truncated,
        )
        restricted_resource_types = SettingsObservedRestrictedResourceTypes(
            namespace=execution_access.namespace,
            observed_at=execution_access.observed_at,
            completeness=execution_access.completeness,
            reason_codes=execution_access.reason_codes,
            items=execution_access.restricted_resource_types,
        )
    revision_payload = {
        "workspace_id": workspace_id,
        "user_id": str(current.user_id),
        "cluster_id": cluster_id,
        "roles": roles,
        "permissions": [
            {"permission": item.permission, "allowed": item.allowed} for item in decisions
        ],
        "kubernetes_rules": kubernetes_rules.model_dump(mode="json"),
        "restricted_resource_types": restricted_resource_types.model_dump(mode="json"),
    }
    revision = hashlib.sha256(
        json.dumps(
            revision_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return SettingsAccessProfileResponse(
        workspace_id=workspace_id,
        user_id=str(current.user_id),
        cluster_id=cluster_id,
        roles=roles,
        permissions=decisions,
        kubernetes_rules=kubernetes_rules,
        restricted_resource_types=restricted_resource_types,
        revision=revision,
    )


class ShellStateRevisionConflict(RuntimeError):
    """Optimistic revision changed before the atomic state/event transaction."""


async def _snapshot_context(db: Any, workspace_id: str, cluster_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        db.filter_snapshot_context,
        workspace_id,
        {cluster_id},
    )


async def _namespace_catalog(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    return await asyncio.to_thread(
        db.list_authorized_namespace_catalog,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        snapshot_revision=int(context.get("snapshot_revision") or 0),
        limit=NAMESPACE_CATALOG_LIMIT,
    )


async def _resolve_persisted_namespaces(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    context: dict[str, Any],
    persisted: dict[str, Any] | None,
) -> set[str]:
    namespaces = tuple((persisted or {}).get("namespaces") or ())
    if not namespaces:
        return set()
    return await asyncio.to_thread(
        db.resolve_authorized_namespaces,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        snapshot_revision=int(context.get("snapshot_revision") or 0),
        namespaces=namespaces,
    )


def _namespace_response(
    *,
    workspace_id: str,
    cluster_id: str,
    context: dict[str, Any],
    catalog: dict[str, Any],
    persisted: dict[str, Any] | None,
    authorized_actives: set[str],
) -> NamespaceScopeResponse:
    accessible = tuple(str(item) for item in catalog.get("items") or ())
    persisted_namespaces = tuple(
        namespace
        for namespace in (persisted or {}).get("namespaces", ())
        if namespace in authorized_actives
    )
    accessible = tuple(sorted(set(accessible).union(persisted_namespaces)))
    revision = int((persisted or {}).get("revision") or 0)
    generation = int((persisted or {}).get("invalidation_generation") or 0)
    snapshot_revision = int(context.get("snapshot_revision") or 0)
    complete = bool(catalog.get("complete")) and bool(context.get("resources_complete"))
    reason_codes = set(str(item) for item in context.get("partial_reason_codes") or ())
    if not bool(catalog.get("complete")):
        reason_codes.add("namespace_catalog_limit_reached")
    if snapshot_revision <= 0:
        completeness = "unavailable"
        reason_codes.add("inventory_snapshot_unavailable")
    else:
        completeness = "exact" if complete else "partial"
    return NamespaceScopeResponse(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        actives=persisted_namespaces,
        mode="selected" if persisted_namespaces else "all",
        accessible_namespaces=accessible,
        accessible_namespace_count=int(catalog.get("total") or 0),
        can_clear_namespace=bool(persisted_namespaces),
        revision=revision,
        invalidation_generation=generation,
        freshness=ShellFreshness(
            observed_at=context.get("observed_at"),
            completeness=completeness,
            reason_codes=tuple(sorted(reason_codes)),
        ),
    )


def _require_inventory_read(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )


def _workspace_id(current: Any) -> str:
    return str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID))
