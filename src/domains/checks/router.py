"""HTTP boundaries for evidence-first Checks reads."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from domains.checks.events import ChecksSettingsUpdatedBody
from domains.checks.observation_projection import checks_detail, checks_overview
from domains.identity.dependencies import require_session, resolve_allowed_cluster_ids
from domains.inventory_filter.query import parse_facet_values
from domains.target.cluster_visibility import visible_allowed_cluster_ids
from packages.contracts.auth import Actor
from packages.contracts.checks import (
    ChecksSettingsPolicy,
    ChecksSettingsResponse,
    ChecksSettingsUpdateRequest,
    ChecksSettingsUpdateResponse,
)
from packages.contracts.checks.observations import ChecksDetailResponse, ChecksOverviewResponse
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    OrganizationRole,
    Permission,
)
from packages.contracts.parity import ResourceRef
from packages.runtime.dependencies import get_db, get_events

router = APIRouter()

INVALID_SCOPE_DETAIL = "Checks scope is invalid"
INVALID_RESOURCE_DETAIL = "Checks resource identity is invalid"
SCOPE_NOT_FOUND_DETAIL = "Checks scope not found"
INVALID_CHECK_ID_DETAIL = "Check identifier is invalid"
CHECKS_SETTINGS_UNAVAILABLE_DETAIL = "Checks settings repository is unavailable"
CHECKS_SETTINGS_FORBIDDEN_DETAIL = "Checks settings update is not allowed"
CHECKS_SETTINGS_CONFLICT_DETAIL = "Checks settings revision conflict"
CHECKS_SETTINGS_NAMESPACE_FORBIDDEN_DETAIL = "Checks settings contain inaccessible namespaces"
CHECKS_SETTINGS_NAMESPACE_UNAVAILABLE_DETAIL = "Checks namespace authority is unavailable"


@router.get(gateway_routes.CHECKS_OVERVIEW_PATH, response_model=ChecksOverviewResponse)
async def get_checks_overview(
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    resource_group: str | None = Query(default=None),
    resource_version: str | None = Query(default=None),
    resource_kind: str | None = Query(default=None),
    resource_namespace: str | None = Query(default=None),
    resource_name: str | None = Query(default=None),
    resource_uid: str | None = Query(default=None),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ChecksOverviewResponse:
    """Read authorized Checks evidence persisted from outbound agents."""

    resource = _resource_ref(
        api_group=resource_group,
        version=resource_version,
        kind=resource_kind,
        namespace=resource_namespace,
        name=resource_name,
        uid=resource_uid,
    )
    scope = await _authorized_scope(
        clusters=clusters, namespaces=namespaces, current=current, db=db
    )
    if resource is not None:
        selected_cluster_ids = tuple(scope["selected_cluster_ids"])
        if len(selected_cluster_ids) != 1:
            raise HTTPException(status_code=422, detail=INVALID_RESOURCE_DETAIL)
        if resource.namespace is not None and (
            selected_cluster_ids[0],
            resource.namespace,
        ) not in set(scope["namespace_refs"]):
            raise HTTPException(status_code=422, detail=INVALID_RESOURCE_DETAIL)
        scope["resource"] = resource
        scope["resource_cluster_id"] = selected_cluster_ids[0]
    return checks_overview(**scope)


@router.get(gateway_routes.CHECKS_DETAIL_PATH, response_model=ChecksDetailResponse)
async def get_checks_detail(
    check_id: str = Path(min_length=1, max_length=253),
    clusters: str | None = Query(default=None),
    namespaces: str | None = Query(default=None),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ChecksDetailResponse:
    """Resolve a direct check URL against the persisted agent catalog."""

    requested_check_id = _check_id(check_id)
    scope = await _authorized_scope(
        clusters=clusters, namespaces=namespaces, current=current, db=db
    )
    return checks_detail(requested_check_id=requested_check_id, **scope)


@router.get(
    gateway_routes.CHECKS_SETTINGS_PATH,
    response_model=ChecksSettingsResponse,
)
async def get_checks_settings(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ChecksSettingsResponse:
    """Read the current user's workspace-bound hidden Checks policy."""

    workspace_id = _workspace_id(current)
    persisted, can_edit = await asyncio.gather(
        _read_checks_settings(db, workspace_id, current.user_id),
        asyncio.to_thread(_can_edit_checks_settings, db, current, workspace_id),
    )
    return _settings_response(
        workspace_id=workspace_id,
        user_id=current.user_id,
        persisted=persisted,
        can_edit=can_edit,
    )


@router.put(
    gateway_routes.CHECKS_SETTINGS_PATH,
    response_model=ChecksSettingsUpdateResponse,
)
async def update_checks_settings(
    payload: ChecksSettingsUpdateRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> ChecksSettingsUpdateResponse:
    """Commit one owner-authorized Checks policy under optimistic revision control."""

    workspace_id = _workspace_id(current)
    if not await asyncio.to_thread(_can_edit_checks_settings, db, current, workspace_id):
        raise HTTPException(status_code=403, detail=CHECKS_SETTINGS_FORBIDDEN_DETAIL)
    await _require_authorized_hidden_namespaces(
        db=db,
        current=current,
        workspace_id=workspace_id,
        references=payload.policy.hidden_namespaces,
    )
    persisted: dict[str, Any] | None = None
    policy = payload.policy.model_dump(mode="json")

    def stage(conn: Any, _event: Any) -> None:
        nonlocal persisted
        writer = getattr(db, "put_checks_settings", None)
        if not callable(writer):
            raise ChecksSettingsRepositoryUnavailable
        persisted = writer(
            workspace_id=workspace_id,
            user_id=current.user_id,
            policy=policy,
            expected_revision=payload.expected_revision,
            conn=conn,
        )
        if persisted is None:
            raise ChecksSettingsRevisionConflict

    try:
        accepted = await events.accept_body(
            ChecksSettingsUpdatedBody(
                workspace_id=workspace_id,
                user_id=current.user_id,
                policy=policy,
                expected_revision=payload.expected_revision,
            ),
            actor=Actor(current.user_id, tuple(current.roles)),
            transactional_stage=stage,
        )
    except ChecksSettingsRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=CHECKS_SETTINGS_CONFLICT_DETAIL) from exc
    except ChecksSettingsRepositoryUnavailable as exc:
        raise HTTPException(status_code=503, detail=CHECKS_SETTINGS_UNAVAILABLE_DETAIL) from exc
    if persisted is None:
        raise RuntimeError("Checks settings transaction did not persist state")
    response = _settings_response(
        workspace_id=workspace_id,
        user_id=current.user_id,
        persisted=persisted,
        can_edit=True,
    )
    event_id = str(accepted.event.event_id)
    return ChecksSettingsUpdateResponse(
        **response.model_dump(),
        event_id=event_id,
        audit_event_id=event_id,
    )


async def _authorized_scope(
    *,
    clusters: str | None,
    namespaces: str | None,
    current: Any,
    db: Any,
) -> dict[str, object]:
    requested_clusters = _selected("clusters", clusters)
    requested_namespaces = _namespace_refs(namespaces)
    workspace_id = _workspace_id(current)
    allowed_clusters = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    requested_scope_clusters = set(requested_clusters) | {
        cluster_id for cluster_id, _namespace in requested_namespaces
    }
    # 인가 검증은 전체 allowed로 유지(보안 불변). 다만 명시 요청이 없을 때의 기본 scope는
    # /clusters 목록과 동일하게 blocked-test 클러스터를 숨겨 scope count 불일치를 없앤다.
    _require_requested_clusters(requested_scope_clusters, allowed_clusters)
    if requested_scope_clusters:
        selected_cluster_ids = tuple(sorted(requested_scope_clusters))
    else:
        visible_clusters = await asyncio.to_thread(
            visible_allowed_cluster_ids, db, workspace_id, allowed_clusters
        )
        selected_cluster_ids = tuple(sorted(visible_clusters))
    contexts, snapshots, persisted_settings = await asyncio.gather(
        asyncio.to_thread(
            db.filter_snapshot_contexts,
            workspace_id,
            selected_cluster_ids,
        ),
        asyncio.to_thread(
            db.latest_inventory_snapshots,
            workspace_id,
            set(selected_cluster_ids),
        ),
        _read_checks_settings(db, workspace_id, current.user_id),
    )
    return {
        "workspace_id": workspace_id,
        "contexts": contexts,
        "snapshots": snapshots,
        "namespace_refs": requested_namespaces,
        "selected_cluster_ids": selected_cluster_ids,
        "settings": _settings_policy(persisted_settings),
    }


async def _read_checks_settings(
    db: Any,
    workspace_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    reader = getattr(db, "get_checks_settings", None)
    if not callable(reader):
        raise HTTPException(status_code=503, detail=CHECKS_SETTINGS_UNAVAILABLE_DETAIL)
    return await asyncio.to_thread(
        reader,
        workspace_id=workspace_id,
        user_id=user_id,
    )


def _settings_policy(persisted: dict[str, Any] | None) -> ChecksSettingsPolicy:
    if persisted is None:
        return ChecksSettingsPolicy()
    return ChecksSettingsPolicy.model_validate(persisted.get("policy") or {})


def _settings_response(
    *,
    workspace_id: str,
    user_id: str,
    persisted: dict[str, Any] | None,
    can_edit: bool,
) -> ChecksSettingsResponse:
    return ChecksSettingsResponse(
        workspace_id=workspace_id,
        user_id=user_id,
        policy=_settings_policy(persisted),
        revision=int((persisted or {}).get("revision") or 0),
        invalidation_generation=int((persisted or {}).get("invalidation_generation") or 0),
        can_edit=can_edit,
        updated_at=(persisted or {}).get("updated_at"),
    )


def _can_edit_checks_settings(db: Any, current: Any, workspace_id: str) -> bool:
    user_id = str(getattr(current, "user_id", "") or "")
    admin_reader = getattr(db, "is_service_admin", None)
    if callable(admin_reader) and bool(admin_reader(user_id)):
        return True
    member_reader = getattr(db, "get_organization_member", None)
    if not callable(member_reader):
        return False
    member = member_reader(workspace_id, user_id)
    return isinstance(member, dict) and member.get("role") == OrganizationRole.OWNER.value


async def _require_authorized_hidden_namespaces(
    *,
    db: Any,
    current: Any,
    workspace_id: str,
    references: tuple[str, ...],
) -> None:
    if not references:
        return
    grouped: dict[str, set[str]] = {}
    for reference in references:
        cluster_id, _, namespace = reference.partition("/")
        grouped.setdefault(cluster_id, set()).add(namespace)
    allowed = await asyncio.to_thread(
        resolve_allowed_cluster_ids,
        db,
        current,
        workspace_id,
        Permission.INVENTORY_READ.value,
    )
    if not set(grouped).issubset(allowed):
        raise HTTPException(status_code=403, detail=CHECKS_SETTINGS_NAMESPACE_FORBIDDEN_DETAIL)
    context_reader = getattr(db, "filter_snapshot_contexts", None)
    resolver = getattr(db, "resolve_authorized_namespaces", None)
    if not callable(context_reader) or not callable(resolver):
        raise HTTPException(
            status_code=503,
            detail=CHECKS_SETTINGS_NAMESPACE_UNAVAILABLE_DETAIL,
        )
    contexts = await asyncio.to_thread(
        context_reader,
        workspace_id,
        tuple(sorted(grouped)),
    )
    for cluster_id, namespaces in grouped.items():
        context = contexts.get(cluster_id) if isinstance(contexts, dict) else None
        revision = int((context or {}).get("snapshot_revision") or 0)
        if revision <= 0:
            raise HTTPException(
                status_code=409,
                detail=CHECKS_SETTINGS_NAMESPACE_UNAVAILABLE_DETAIL,
            )
        resolved = await asyncio.to_thread(
            resolver,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            snapshot_revision=revision,
            namespaces=tuple(sorted(namespaces)),
        )
        if set(resolved) != namespaces:
            raise HTTPException(
                status_code=403,
                detail=CHECKS_SETTINGS_NAMESPACE_FORBIDDEN_DETAIL,
            )


def _workspace_id(current: Any) -> str:
    return str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID) or DEFAULT_WORKSPACE_ID)


def _selected(axis: str, value: str | None) -> tuple[str, ...]:
    try:
        return parse_facet_values(axis, value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_SCOPE_DETAIL) from exc


def _namespace_refs(value: str | None) -> tuple[tuple[str, str], ...]:
    selected = _selected("namespaces", value)
    return tuple(token.rpartition("/")[::2] for token in selected)


def _resource_ref(
    *,
    api_group: str | None,
    version: str | None,
    kind: str | None,
    namespace: str | None,
    name: str | None,
    uid: str | None,
) -> ResourceRef | None:
    raw = (api_group, version, kind, namespace, name, uid)
    if all(value is None for value in raw):
        return None
    required = (version, kind, name, uid)
    if any(value is None or value.strip() != value or not value for value in required):
        raise HTTPException(status_code=422, detail=INVALID_RESOURCE_DETAIL)
    if any(
        value is not None and (value.strip() != value or any(ord(char) < 32 for char in value))
        for value in raw
    ):
        raise HTTPException(status_code=422, detail=INVALID_RESOURCE_DETAIL)
    try:
        return ResourceRef(
            api_group=api_group or "",
            version=version or "",
            kind=kind or "",
            namespace=namespace or None,
            name=name or "",
            uid=uid or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=INVALID_RESOURCE_DETAIL) from exc


def _require_requested_clusters(requested: Iterable[str], allowed: set[str]) -> None:
    if not set(requested).issubset(allowed):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


def _check_id(value: str) -> str:
    normalized = value.strip()
    if (
        normalized != value
        or not normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise HTTPException(status_code=422, detail=INVALID_CHECK_ID_DETAIL)
    return normalized


class ChecksSettingsRevisionConflict(Exception):
    """The persisted revision changed before the transactional write."""


class ChecksSettingsRepositoryUnavailable(Exception):
    """The transactional settings repository is not installed."""
