"""Reusable, evidence-first projection of inventory snapshot scope."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from packages.contracts.parity import ClusterScope

SnapshotScopeAvailability = Literal["available", "partial", "unavailable"]


@dataclass(frozen=True)
class SnapshotScopeProjection:
    """Authorized selected scope and the freshness of its source snapshots."""

    availability: SnapshotScopeAvailability
    scopes: tuple[ClusterScope, ...]
    observed_at: str | None
    reason_codes: tuple[str, ...]


def project_snapshot_scope(
    *,
    workspace_id: str,
    contexts: Mapping[str, Mapping[str, Any]],
    namespace_refs: Iterable[tuple[str, str]],
    selected_cluster_ids: Iterable[str],
) -> SnapshotScopeProjection:
    """Describe scope without implying that a feature-specific collector exists."""

    selected = tuple(sorted({_text(value) for value in selected_cluster_ids if _text(value)}))
    namespaces = _namespaces_by_cluster(namespace_refs)
    if not selected:
        return SnapshotScopeProjection(
            availability="unavailable",
            scopes=(),
            observed_at=None,
            reason_codes=("authorization_scope_empty",),
        )

    reasons: set[str] = set()
    observed_at: list[str] = []
    scopes: list[ClusterScope] = []
    has_unavailable = False
    has_partial = False
    for cluster_id in selected:
        context = contexts.get(cluster_id)
        if context is None or int(context.get("snapshot_revision") or 0) <= 0:
            has_unavailable = True
            reasons.add(f"inventory_snapshot_unavailable:{cluster_id}")
        elif not bool(context.get("resources_complete")) or not bool(
            context.get("labels_complete")
        ):
            has_partial = True
            reasons.add(f"inventory_snapshot_incomplete:{cluster_id}")
        for reason in (context or {}).get("partial_reason_codes", ()):
            if normalized := _text(reason):
                has_partial = True
                reasons.add(normalized)
        if stamp := _optional_text((context or {}).get("observed_at")):
            observed_at.append(stamp)
        scopes.append(
            ClusterScope(
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                namespaces=namespaces.get(cluster_id, ()),
                freshness=_freshness(context),
            )
        )

    availability: SnapshotScopeAvailability = (
        "unavailable" if has_unavailable else "partial" if has_partial else "available"
    )
    return SnapshotScopeProjection(
        availability=availability,
        scopes=tuple(scopes),
        observed_at=max(observed_at) if observed_at else None,
        reason_codes=tuple(sorted(reasons)),
    )


def _freshness(context: Mapping[str, Any] | None) -> str:
    if context is None or int(context.get("snapshot_revision") or 0) <= 0:
        return "disconnected"
    if not bool(context.get("resources_complete")) or not bool(context.get("labels_complete")):
        return "partial"
    return "live"


def _namespaces_by_cluster(
    namespace_refs: Iterable[tuple[str, str]],
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, set[str]] = {}
    for cluster_id, namespace in namespace_refs:
        if normalized_cluster := _text(cluster_id):
            if normalized_namespace := _text(namespace):
                grouped.setdefault(normalized_cluster, set()).add(normalized_namespace)
    return {cluster_id: tuple(sorted(values)) for cluster_id, values in grouped.items()}


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_text(value: object) -> str | None:
    return _text(value) or None
