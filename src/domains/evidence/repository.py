"""Cluster 권한을 SQL에 강제하는 evidence 읽기 저장소."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select

from domains.identity.models import ClusterRegistration
from domains.rca.models import Evidence
from domains.target.models import EvidenceWindow
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection, iso_or_none


def _cluster_ids(allowed_cluster_ids: Collection[str] | None) -> tuple[str, ...]:
    """인가 결과를 결정적인 non-empty ID 집합으로 정규화한다."""
    if not allowed_cluster_ids:
        return ()
    return tuple(
        sorted(
            {
                cluster_id.strip()
                for cluster_id in allowed_cluster_ids
                if isinstance(cluster_id, str) and cluster_id.strip()
            }
        )
    )


class EvidenceQueryRepository(DatabaseConnection):
    """Raw evidence 조회에 workspace+cluster 경계를 함께 적용한다."""

    def list_workspace_cluster_ids(self, workspace_id: str) -> set[str]:
        """관리자 wildcard 대신 사용할 workspace 내 명시적 cluster ID 집합."""
        if not workspace_id:
            return set()
        table = ClusterRegistration.__table__
        statement = select(table.c.cluster_id).where(table.c.workspace_id == workspace_id)
        with self.connection() as conn:
            return {str(cluster_id) for cluster_id in conn.execute(statement).scalars().all()}

    def list_evidence(
        self,
        workspace_id: str,
        allowed_cluster_ids: Collection[str] | None,
        *,
        correlation_id: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        cursor: tuple[datetime, int] | None = None,
    ) -> list[JsonObject]:
        cluster_ids = _cluster_ids(allowed_cluster_ids)
        if not workspace_id or not cluster_ids:
            return []

        table = Evidence.__table__
        statement: Select[Any] = (
            select(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.payload["cluster_id"].astext.in_(cluster_ids),
            )
            .order_by(table.c.created_at.desc(), table.c.id.desc())
            .limit(limit)
        )
        if correlation_id is not None:
            statement = statement.where(table.c.correlation_id == correlation_id)
        if kind is not None:
            statement = statement.where(table.c.kind == kind)
        if since is not None:
            statement = statement.where(table.c.created_at >= since)
        if until is not None:
            statement = statement.where(table.c.created_at < until)
        statement = _apply_evidence_cursor(statement, table, cursor, offset)

        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_serialize_evidence(row) for row in rows]

    def list_evidence_windows(
        self,
        workspace_id: str,
        allowed_cluster_ids: Collection[str] | None,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[JsonObject]:
        cluster_ids = _cluster_ids(allowed_cluster_ids)
        if not workspace_id or not cluster_ids:
            return []

        table = EvidenceWindow.__table__
        statement = (
            select(
                table.c.evidence_key,
                table.c.workspace_id,
                table.c.cluster_id,
                table.c.source_id,
                table.c.window_start,
                table.c.agent_id,
                table.c.correlation_id,
                table.c.payload,
                table.c.created_at,
                table.c.updated_at,
            )
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id.in_(cluster_ids),
            )
            .order_by(table.c.updated_at.desc(), table.c.evidence_key.desc())
            .limit(limit)
            .offset(offset)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def list_latest_traffic_evidence_windows(
        self,
        workspace_id: str,
        allowed_cluster_ids: Collection[str] | None,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> list[JsonObject]:
        """Read at most one persisted Agent metrics window per authorized cluster."""

        cluster_ids = _cluster_ids(allowed_cluster_ids)
        if not workspace_id or not cluster_ids:
            return []
        table = EvidenceWindow.__table__
        rank = (
            func.row_number()
            .over(
                partition_by=table.c.cluster_id,
                order_by=(table.c.updated_at.desc(), table.c.evidence_key.desc()),
            )
            .label("traffic_window_rank")
        )
        ranked = select(
            table.c.evidence_key,
            table.c.workspace_id,
            table.c.cluster_id,
            table.c.source_id,
            table.c.window_start,
            table.c.agent_id,
            table.c.correlation_id,
            table.c.payload,
            table.c.created_at,
            table.c.updated_at,
            rank,
        ).where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id.in_(cluster_ids),
            table.c.source_id == "cluster-snapshot",
            table.c.updated_at >= since,
        )
        if until is not None:
            ranked = ranked.where(table.c.updated_at <= until)
        ranked_rows = ranked.subquery()
        statement = (
            select(
                ranked_rows.c.evidence_key,
                ranked_rows.c.workspace_id,
                ranked_rows.c.cluster_id,
                ranked_rows.c.source_id,
                ranked_rows.c.window_start,
                ranked_rows.c.agent_id,
                ranked_rows.c.correlation_id,
                ranked_rows.c.payload,
                ranked_rows.c.created_at,
                ranked_rows.c.updated_at,
            )
            .where(ranked_rows.c.traffic_window_rank == 1)
            .order_by(ranked_rows.c.cluster_id)
            .limit(len(cluster_ids))
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def get_evidence(
        self,
        workspace_id: str,
        evidence_key: str,
        allowed_cluster_ids: Collection[str] | None,
    ) -> JsonObject | None:
        cluster_ids = _cluster_ids(allowed_cluster_ids)
        if not workspace_id or not evidence_key or not cluster_ids:
            return None

        table = EvidenceWindow.__table__
        statement = select(table.c.payload).where(
            table.c.workspace_id == workspace_id,
            table.c.evidence_key == evidence_key,
            table.c.cluster_id.in_(cluster_ids),
        )
        with self.connection() as conn:
            payload = conn.execute(statement).scalar_one_or_none()
        return payload if isinstance(payload, dict) else None


def _apply_evidence_cursor(
    statement: Select[Any],
    table: Any,
    cursor: tuple[datetime, int] | None,
    offset: int,
) -> Select[Any]:
    if cursor is None:
        return statement.offset(offset)
    created_at, row_id = cursor
    return statement.where(
        or_(
            table.c.created_at < created_at,
            and_(table.c.created_at == created_at, table.c.id < row_id),
        )
    )


def _serialize_evidence(row: Any) -> JsonObject:
    item = dict(row)
    item["created_at"] = iso_or_none(item.get("created_at"))
    return item
