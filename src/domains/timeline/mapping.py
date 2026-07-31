"""Lossless source-to-timeline mappings.

These mappers are intentionally explicit about identity.  In particular, an
inventory version without a Kubernetes UID becomes a locator subject; it is
never converted into a made-up ``ResourceRef``.
"""

from __future__ import annotations

from datetime import datetime

from packages.contracts.parity import ClusterScope, Freshness, ResourceRef
from packages.contracts.timeline import (
    TimelineEvent,
    TimelineInventoryLocatorSubject,
    TimelineResourceSubject,
)
from packages.contracts.timeline.models import TimelineEventType, TimelineSeverity


def inventory_timeline_event(
    *,
    event_id: str,
    source_key: str,
    native_id: str,
    occurred_at: datetime,
    workspace_id: str,
    cluster_id: str,
    api_version: str,
    resource_kind: str,
    namespace: str | None,
    name: str,
    uid: str | None,
    title: str,
    event_type: TimelineEventType = "update",
    severity: TimelineSeverity = "info",
    freshness: Freshness = "live",
    metadata: dict[str, object] | None = None,
) -> TimelineEvent:
    """Map one inventory version using its observed UID only when it truly exists."""
    api_group, version = split_api_version(api_version)
    scope = ClusterScope(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        freshness=freshness,
    )
    normalized_uid = (uid or "").strip()
    if normalized_uid:
        resource = ResourceRef(
            api_group=api_group,
            version=version,
            kind=resource_kind,
            namespace=namespace,
            name=name,
            uid=normalized_uid,
        )
        subject = TimelineResourceSubject(resource=resource)
    else:
        resource = None
        subject = TimelineInventoryLocatorSubject(
            inventory_key=native_id,
            api_group=api_group,
            version=version,
            resource_kind=resource_kind,
            namespace=namespace,
            name=name,
        )
    return TimelineEvent(
        event_id=event_id,
        source="inventory",
        source_key=source_key,
        native_id=native_id,
        activity="change",
        occurred_at=occurred_at,
        scope=scope,
        subject=subject,
        resource=resource,
        event_type=event_type,
        severity=severity,
        title=title,
        metadata=dict(metadata or {}),
    )


def split_api_version(api_version: str) -> tuple[str, str]:
    """Split Kubernetes ``group/version`` without inventing either half."""
    normalized = api_version.strip()
    if "/" not in normalized:
        return "", normalized
    group, version = normalized.rsplit("/", 1)
    return group, version
