"""BQ-061 — exact inventory resource의 실행 가능한 action capability 판정."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException

from domains.identity.dependencies import require_cluster_access
from domains.inventory.action_catalog import applicable_resource_actions
from domains.inventory.workload_revisions import workload_rollback_available
from packages.contracts.gateway.responses import (
    ResourceCapabilitiesResponse,
    ResourceCapabilitySubject,
)

CONNECTED_AGENT_STATUS = "connected"


def resource_capabilities_response(
    db: Any,
    *,
    workspace_id: str,
    current: Any,
    resource: dict[str, Any],
) -> ResourceCapabilitiesResponse:
    """실제 route가 수용할 조건을 모두 만족하는 action만 반환한다."""
    subject = _subject(resource)
    applicable = tuple(
        definition
        for definition in applicable_resource_actions(subject, resource)
        if definition.capability_id != "workload.rollback"
        or workload_rollback_available(db, workspace_id=workspace_id, resource=resource)
    )
    required_agent_capabilities = {definition.agent_capability for definition in applicable}
    agent_support = {
        capability: _agent_supports_capability(db, workspace_id, subject.cluster_id, capability)
        for capability in required_agent_capabilities
    }
    supported = tuple(
        definition for definition in applicable if agent_support[definition.agent_capability]
    )
    required_permissions = {definition.permission for definition in supported}
    permissions = {
        permission: _has_cluster_permission(
            db,
            current=current,
            workspace_id=workspace_id,
            cluster_id=subject.cluster_id,
            permission=permission,
        )
        for permission in required_permissions
    }
    capabilities = [
        definition.render(subject) for definition in supported if permissions[definition.permission]
    ]
    capabilities.sort(key=lambda item: item.capability_id)

    revision = _revision(
        workspace_id=workspace_id,
        current=current,
        subject=subject,
        applicable_ids=[definition.capability_id for definition in applicable],
        permissions=permissions,
        agent_support=agent_support,
        capability_ids=[item.capability_id for item in capabilities],
    )
    return ResourceCapabilitiesResponse(
        subject=subject,
        revision=revision,
        capabilities=capabilities,
    )


def _subject(resource: dict[str, Any]) -> ResourceCapabilitySubject:
    return ResourceCapabilitySubject(
        resource_id=str(resource["inventory_key"]),
        snapshot_id=str(resource["snapshot_id"]),
        cluster_id=str(resource["cluster_id"]),
        resource_type=str(resource["resource_type"]).strip().lower(),
        kind=str(resource["kind"]),
        namespace=(str(resource["namespace"]) if resource.get("namespace") is not None else None),
        name=str(resource["name"]),
    )


def _has_cluster_permission(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    permission: str,
) -> bool:
    try:
        require_cluster_access(db, current, workspace_id, cluster_id, permission)
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


def _agent_supports_capability(
    db: Any, workspace_id: str, cluster_id: str, capability: str
) -> bool:
    statuses_reader = getattr(db, "list_cluster_agent_statuses", None)
    if not callable(statuses_reader):
        return False
    statuses = statuses_reader(workspace_id, cluster_id)
    return any(
        str(item.get("status") or "") == CONNECTED_AGENT_STATUS
        and capability in tuple(item.get("capabilities") or ())
        for item in statuses
        if isinstance(item, dict)
    )


def _revision(
    *,
    workspace_id: str,
    current: Any,
    subject: ResourceCapabilitySubject,
    applicable_ids: list[str],
    permissions: dict[str, bool],
    agent_support: dict[str, bool],
    capability_ids: list[str],
) -> str:
    payload = {
        "workspace_id": workspace_id,
        "actor_id": str(getattr(current, "user_id", "")),
        "roles": sorted(set(str(role) for role in (getattr(current, "roles", ()) or ()))),
        "subject": subject.model_dump(),
        "applicable_ids": applicable_ids,
        "permissions": permissions,
        "agent_support": agent_support,
        "capability_ids": capability_ids,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()
