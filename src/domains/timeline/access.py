"""Shared authorization for timeline-shaped, multi-source reads.

The Resources change strip and the full Timeline consume the same bounded
inventory, incident, and deployment evidence.  Keeping source-specific grants
in one place prevents a later Timeline endpoint from accidentally treating an
inventory grant as an RCA or deployment grant.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from domains.identity.dependencies import (
    resolve_allowed_application_ids,
    resolve_allowed_cluster_ids,
)
from domains.inventory_filter.query import ResourceFilters
from packages.contracts.identity import Permission

SCOPE_NOT_FOUND_DETAIL = "change timeline scope not found"


@dataclass(frozen=True)
class AuthorizedTimelineScope:
    """Grants kept separate until a source is actually queried."""

    workspace_id: str
    user_id: str
    roles: tuple[str, ...]
    cluster_ids: frozenset[str]
    application_ids: frozenset[str]
    incident_cluster_ids: frozenset[str]
    deployment_application_ids: frozenset[str]

    @property
    def readable_cluster_ids(self) -> frozenset[str]:
        """All cluster identities that a source-specific reader may select."""
        return self.cluster_ids | self.incident_cluster_ids

    @property
    def authorization_revision(self) -> str:
        """Invalidate timeline cursors whenever any source grant changes."""
        payload = {
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "roles": sorted(set(self.roles)),
            "inventory_clusters": sorted(self.cluster_ids),
            "application_ids": sorted(self.application_ids),
            "incident_clusters": sorted(self.incident_cluster_ids),
            "deployment_application_ids": sorted(self.deployment_application_ids),
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def require_timeline_capability_access(authorized: AuthorizedTimelineScope) -> None:
    """Hide Timeline capability metadata when no retained source is readable.

    The descriptor carries no grants itself.  This guard keeps the first
    browser read in the same authenticated workspace/source boundary as later
    Timeline snapshots without requiring a query, scope, or cursor.
    """
    if not (
        authorized.readable_cluster_ids
        or authorized.application_ids
        or authorized.deployment_application_ids
    ):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


async def resolve_authorized_timeline_scope(db: Any, current: Any) -> AuthorizedTimelineScope:
    """Resolve all source grants without widening one source through another."""
    workspace_id = str(getattr(current, "workspace_id", "") or "").strip()
    user_id = str(getattr(current, "user_id", "") or "").strip()
    roles = tuple(str(role) for role in (getattr(current, "roles", ()) or ()))
    if not workspace_id or not user_id:
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)

    def resolve() -> tuple[set[str], set[str], set[str], set[str]]:
        return (
            resolve_allowed_cluster_ids(
                db,
                current,
                workspace_id,
                Permission.INVENTORY_READ.value,
            ),
            resolve_allowed_application_ids(
                db,
                current,
                workspace_id,
                Permission.APPLICATION_READ.value,
            ),
            resolve_allowed_cluster_ids(
                db,
                current,
                workspace_id,
                Permission.RCA_READ.value,
            ),
            resolve_allowed_application_ids(
                db,
                current,
                workspace_id,
                Permission.DEPLOYMENT_READ.value,
            ),
        )

    clusters, applications, incident_clusters, deployment_applications = await asyncio.to_thread(
        resolve
    )
    return AuthorizedTimelineScope(
        workspace_id=workspace_id,
        user_id=user_id,
        roles=roles,
        cluster_ids=frozenset(clusters),
        application_ids=frozenset(applications),
        incident_cluster_ids=frozenset(incident_clusters),
        deployment_application_ids=frozenset(deployment_applications),
    )


def require_requested_timeline_scope(
    authorized: AuthorizedTimelineScope,
    filters: ResourceFilters,
) -> None:
    """Reject a forbidden requested scope before any timeline query executes."""
    requested_clusters = set(filters.clusters) | {
        cluster_id for cluster_id, _namespace in filters.namespaces
    }
    if not requested_clusters.issubset(authorized.cluster_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)
    if not set(filters.applications).issubset(authorized.application_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)


def selected_timeline_cluster_ids(
    authorized: AuthorizedTimelineScope,
    filters: ResourceFilters,
) -> set[str]:
    """Return the selected inventory-visible clusters after authorization."""
    selected = set(filters.clusters) | {cluster_id for cluster_id, _namespace in filters.namespaces}
    return selected if selected else set(authorized.cluster_ids)


def require_timeline_cluster_ids(
    authorized: AuthorizedTimelineScope,
    requested_cluster_ids: set[str],
) -> set[str]:
    """Fail closed before a full Timeline query reads any durable evidence.

    The selected identity may be readable through inventory or incident
    authority.  Application-only authority is intentionally not treated as a
    blanket cluster grant; application events carry their own identity and are
    filtered separately by the ledger adapter.
    """
    if not requested_cluster_ids.issubset(authorized.readable_cluster_ids):
        raise HTTPException(status_code=404, detail=SCOPE_NOT_FOUND_DETAIL)
    return requested_cluster_ids
