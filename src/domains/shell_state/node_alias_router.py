"""Session/RBAC guarded node display alias APIs."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path

from domains.identity.dependencies import require_cluster_access, require_session
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.node_aliases import (
    NodeAliasItem,
    NodeAliasListResponse,
    NodeAliasUpdateRequest,
)
from packages.runtime.dependencies import get_db

router = APIRouter()
NODE_NOT_FOUND_DETAIL = "node not found"
OBSERVED_NODE_KIND = "Node"
OBSERVED_NODE_RESOURCE_TYPE = "node"


@router.get(
    gateway_routes.CLUSTER_NODE_ALIASES_PATH,
    response_model=NodeAliasListResponse,
)
async def list_node_aliases(
    cluster_id: str = Path(min_length=1, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> NodeAliasListResponse:
    workspace_id = _workspace_id(current)
    _require_inventory_read(db, current, workspace_id, cluster_id)
    aliases = await asyncio.to_thread(
        db.list_node_aliases,
        workspace_id=workspace_id,
        user_id=current.user_id,
        cluster_id=cluster_id,
    )
    return NodeAliasListResponse(
        cluster_id=cluster_id,
        aliases=tuple(NodeAliasItem.model_validate(alias) for alias in aliases),
    )


@router.put(
    gateway_routes.CLUSTER_NODE_ALIAS_PATH,
    response_model=NodeAliasItem,
)
async def put_node_alias(
    payload: NodeAliasUpdateRequest,
    cluster_id: str = Path(min_length=1, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH),
    node_name: str = Path(min_length=1, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> NodeAliasItem:
    workspace_id = _workspace_id(current)
    _require_inventory_read(db, current, workspace_id, cluster_id)
    await _require_observed_node(db, workspace_id, cluster_id, node_name)
    saved = await asyncio.to_thread(
        db.put_node_alias,
        workspace_id=workspace_id,
        user_id=current.user_id,
        cluster_id=cluster_id,
        node_name=node_name,
        alias=payload.alias,
    )
    return NodeAliasItem.model_validate(saved)


@router.delete(
    gateway_routes.CLUSTER_NODE_ALIAS_PATH,
    status_code=204,
    response_model=None,
)
async def delete_node_alias(
    cluster_id: str = Path(min_length=1, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH),
    node_name: str = Path(min_length=1, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> None:
    workspace_id = _workspace_id(current)
    _require_inventory_read(db, current, workspace_id, cluster_id)
    await asyncio.to_thread(
        db.delete_node_alias,
        workspace_id=workspace_id,
        user_id=current.user_id,
        cluster_id=cluster_id,
        node_name=node_name,
    )


async def _require_observed_node(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    node_name: str,
) -> None:
    node = await asyncio.to_thread(
        db.get_inventory_resource,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=OBSERVED_NODE_RESOURCE_TYPE,
        kind=OBSERVED_NODE_KIND,
        name=node_name,
        namespace=None,
    )
    if node is None:
        raise HTTPException(status_code=404, detail=NODE_NOT_FOUND_DETAIL)


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
