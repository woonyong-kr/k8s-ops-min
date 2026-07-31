"""Bounded reads for Cost observations persisted by outbound cluster agents."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select, union_all

from domains.target.models import EvidenceWindow
from packages.contracts.cost.observations import (
    COST_NAMESPACE_HOURLY_METRIC,
    COST_NAMESPACE_STORAGE_METRIC,
    MAX_COST_TREND_POINTS,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection
from packages.storage.evidence_predicates import (
    cost_evidence_predicate,
    cost_overview_evidence_predicate,
)


def _normalized_cluster_ids(cluster_ids: Collection[str] | None) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                cluster_id.strip()
                for cluster_id in cluster_ids or ()
                if isinstance(cluster_id, str) and cluster_id.strip()
            }
        )
    )


def _bounded_query_scope(
    workspace_id: str,
    cluster_ids: Collection[str] | None,
    limit_per_cluster: int,
) -> tuple[tuple[str, ...], int] | None:
    normalized = _normalized_cluster_ids(cluster_ids)
    if not workspace_id or not normalized:
        return None
    return normalized, min(max(int(limit_per_cluster), 1), MAX_COST_TREND_POINTS)


def cost_evidence_statement(
    *,
    workspace_id: str,
    cluster_ids: tuple[str, ...],
    since: datetime,
    limit_per_cluster: int,
) -> Select[Any]:
    """Use one index-bounded branch per cluster so noisy clusters cannot consume the batch."""

    table = EvidenceWindow.__table__
    metric_filter = cost_evidence_predicate(table.c.payload)
    branches = [
        select(
            table.c.evidence_key,
            table.c.workspace_id,
            table.c.cluster_id,
            table.c.window_start,
            table.c.payload,
            table.c.updated_at,
        )
        .where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id == cluster_id,
            table.c.updated_at >= since,
            metric_filter,
        )
        .order_by(table.c.updated_at.desc(), table.c.evidence_key.desc())
        .limit(limit_per_cluster)
        for cluster_id in cluster_ids
    ]
    bounded = union_all(*branches).subquery("bounded_cost_evidence")
    return select(
        bounded.c.evidence_key,
        bounded.c.workspace_id,
        bounded.c.cluster_id,
        bounded.c.window_start,
        bounded.c.payload,
        bounded.c.updated_at,
    ).order_by(bounded.c.updated_at.asc(), bounded.c.evidence_key.asc())


def cost_overview_evidence_statement(
    *,
    workspace_id: str,
    cluster_ids: tuple[str, ...],
    since: datetime,
    limit_per_cluster: int,
) -> Select[Any]:
    """Read only the JSON fragments required by the namespace Cost overview."""

    table = EvidenceWindow.__table__
    metric_results = table.c.payload["metrics"]["results"]
    overview_filter = cost_overview_evidence_predicate(table.c.payload)
    branches = [
        select(
            table.c.evidence_key,
            table.c.cluster_id,
            table.c.updated_at,
            table.c.payload["cluster_id"].label("payload_cluster_id"),
            metric_results[COST_NAMESPACE_HOURLY_METRIC].label("namespace_hourly"),
            metric_results[COST_NAMESPACE_STORAGE_METRIC].label("namespace_storage"),
        )
        .where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id == cluster_id,
            table.c.updated_at >= since,
            overview_filter,
        )
        .order_by(table.c.updated_at.desc(), table.c.evidence_key.desc())
        .limit(limit_per_cluster)
        for cluster_id in cluster_ids
    ]
    bounded = union_all(*branches).subquery("bounded_cost_overview_evidence")
    return select(
        bounded.c.cluster_id,
        bounded.c.updated_at,
        bounded.c.payload_cluster_id,
        bounded.c.namespace_hourly,
        bounded.c.namespace_storage,
    ).order_by(bounded.c.updated_at.asc(), bounded.c.evidence_key.asc())


class CostObservationRepository(DatabaseConnection):
    """Cost-specific evidence query with workspace, cluster, time, and volume bounds."""

    def list_cost_evidence_windows(
        self,
        workspace_id: str,
        cluster_ids: Collection[str] | None,
        *,
        since: datetime,
        limit_per_cluster: int = MAX_COST_TREND_POINTS,
    ) -> list[JsonObject]:
        scope = _bounded_query_scope(workspace_id, cluster_ids, limit_per_cluster)
        if scope is None:
            return []
        normalized, bounded_limit = scope
        statement = cost_evidence_statement(
            workspace_id=workspace_id,
            cluster_ids=normalized,
            since=since,
            limit_per_cluster=bounded_limit,
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def list_cost_overview_evidence_windows(
        self,
        workspace_id: str,
        cluster_ids: Collection[str] | None,
        *,
        since: datetime,
        limit_per_cluster: int = MAX_COST_TREND_POINTS,
    ) -> list[JsonObject]:
        """Return compact namespace evidence without reading pod allocation payloads."""

        scope = _bounded_query_scope(workspace_id, cluster_ids, limit_per_cluster)
        if scope is None:
            return []
        normalized, bounded_limit = scope
        statement = cost_overview_evidence_statement(
            workspace_id=workspace_id,
            cluster_ids=normalized,
            since=since,
            limit_per_cluster=bounded_limit,
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_overview_evidence_window(row) for row in rows]


def _overview_evidence_window(row: Mapping[str, Any]) -> JsonObject:
    return {
        "cluster_id": row["cluster_id"],
        "updated_at": row["updated_at"],
        "payload": {
            "cluster_id": row["payload_cluster_id"],
            "metrics": {
                "results": {
                    COST_NAMESPACE_HOURLY_METRIC: row["namespace_hourly"],
                    COST_NAMESPACE_STORAGE_METRIC: row["namespace_storage"],
                }
            },
        },
    }
