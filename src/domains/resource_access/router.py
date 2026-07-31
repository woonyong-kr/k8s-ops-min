"""Authorized browser routes for retained Kubernetes RBAC evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from domains.identity.dependencies import require_cluster_access, require_session
from domains.resource_access.projection import (
    ResourceAccessUnavailable,
    access_snapshot_from_inventory,
    namespace_access_projection,
    role_access_projection,
    subject_access_projection,
)
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.resource_access import (
    KubernetesNamespaceAccessResponse,
    KubernetesRoleAccessResponse,
    KubernetesSubjectAccessResponse,
)
from packages.runtime.dependencies import get_db

router = APIRouter()


def _access_snapshot(db: Any, current: Any, cluster_id: str) -> Mapping[str, object]:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
    )
    try:
        return access_snapshot_from_inventory(
            db.latest_inventory_snapshot(workspace_id, cluster_id)
        )
    except ResourceAccessUnavailable as exc:
        raise HTTPException(status_code=503, detail="resource_access_unavailable") from exc


def _project_subject(
    snapshot: Mapping[str, object],
    *,
    kind: str,
    namespace: str | None,
    name: str,
) -> KubernetesSubjectAccessResponse:
    try:
        return subject_access_projection(
            snapshot,
            kind=kind,
            namespace=namespace,
            name=name,
        )
    except ResourceAccessUnavailable as exc:
        raise HTTPException(status_code=503, detail="resource_access_incomplete") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    gateway_routes.RBAC_SUBJECT_NAMESPACED_PATH,
    response_model=KubernetesSubjectAccessResponse,
)
async def get_namespaced_subject_access(
    kind: Literal["ServiceAccount"],
    namespace: str,
    name: str,
    cluster_id: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> KubernetesSubjectAccessResponse:
    return _project_subject(
        _access_snapshot(db, current, cluster_id),
        kind=kind,
        namespace=namespace,
        name=name,
    )


@router.get(
    gateway_routes.RBAC_SUBJECT_GLOBAL_PATH,
    response_model=KubernetesSubjectAccessResponse,
)
async def get_global_subject_access(
    kind: Literal["User", "Group"],
    name: str,
    cluster_id: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> KubernetesSubjectAccessResponse:
    return _project_subject(
        _access_snapshot(db, current, cluster_id),
        kind=kind,
        namespace=None,
        name=name,
    )


@router.get(
    gateway_routes.RBAC_ROLE_PATH,
    response_model=KubernetesRoleAccessResponse,
)
async def get_role_access(
    kind: Literal["Role", "ClusterRole"],
    namespace: str,
    name: str,
    cluster_id: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> KubernetesRoleAccessResponse:
    try:
        return role_access_projection(
            _access_snapshot(db, current, cluster_id),
            kind=kind,
            namespace=None if namespace == "_" else namespace,
            name=name,
        )
    except ResourceAccessUnavailable as exc:
        raise HTTPException(status_code=503, detail="resource_access_incomplete") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    gateway_routes.RBAC_NAMESPACE_PATH,
    response_model=KubernetesNamespaceAccessResponse,
)
async def get_namespace_access(
    namespace: str,
    cluster_id: str = Query(min_length=1),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> KubernetesNamespaceAccessResponse:
    try:
        return namespace_access_projection(
            _access_snapshot(db, current, cluster_id),
            namespace=namespace,
        )
    except ResourceAccessUnavailable as exc:
        raise HTTPException(status_code=503, detail="resource_access_incomplete") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
