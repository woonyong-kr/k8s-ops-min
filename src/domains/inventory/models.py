"""inventory 도메인 테이블 — 멀티 클러스터 Kubernetes 리소스 read model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Index, Integer, Text, and_, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


def live_inventory_snapshot_clause(table: Any) -> Any:
    """Legacy and normal snapshots are fleet truth; scoped RCA snapshots are not."""
    return func.coalesce(
        table.c.summary["summary"]["live_inventory"].as_boolean(),
        True,
    ).is_(True)


def timeline_coverage_snapshot_clause(table: Any) -> Any:
    """Partial-index boundary shared by the retained Timeline coverage query."""
    return and_(
        table.c.status != "ignored_stale",
        table.c.event_capture.is_not(None),
    )


class ClusterInventorySnapshotRecord(Base):
    __tablename__ = "cluster_inventory_snapshots"
    __table_args__ = (
        Index("ix_inventory_snapshots_scope", "workspace_id", "cluster_id", "created_at"),
    )

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    agent_id: Mapped[str] = text_column()
    source: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    collected_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    event_capture_observed_at: Mapped[Any | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    event_capture: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()


Index(
    "ix_inventory_snapshots_live_scope_latest",
    ClusterInventorySnapshotRecord.workspace_id,
    ClusterInventorySnapshotRecord.cluster_id,
    ClusterInventorySnapshotRecord.created_at.desc(),
    ClusterInventorySnapshotRecord.snapshot_id.desc(),
    postgresql_where=live_inventory_snapshot_clause(ClusterInventorySnapshotRecord.__table__),
)

Index(
    "ix_inventory_snapshots_timeline_capture_projection",
    ClusterInventorySnapshotRecord.workspace_id,
    ClusterInventorySnapshotRecord.cluster_id,
    ClusterInventorySnapshotRecord.event_capture_observed_at,
    ClusterInventorySnapshotRecord.collected_at,
    ClusterInventorySnapshotRecord.created_at,
    ClusterInventorySnapshotRecord.snapshot_id,
    postgresql_where=and_(
        timeline_coverage_snapshot_clause(ClusterInventorySnapshotRecord.__table__),
        ClusterInventorySnapshotRecord.event_capture_observed_at.is_not(None),
    ),
    postgresql_include=(
        ClusterInventorySnapshotRecord.status,
        ClusterInventorySnapshotRecord.event_capture,
    ),
)


class ClusterInventoryResourceRecord(Base):
    __tablename__ = "cluster_inventory_resources"
    __table_args__ = (
        Index(
            "ix_inventory_resources_scope",
            "workspace_id",
            "cluster_id",
            "resource_type",
            "namespace",
            "name",
        ),
        Index("ix_inventory_resources_health", "workspace_id", "cluster_id", "health"),
        Index("ix_inventory_resources_deleted", "workspace_id", "cluster_id", "deleted_at"),
        Index(
            "ix_inventory_resources_helm_ownership",
            "workspace_id",
            "cluster_id",
            "namespace",
            text("(annotations ->> 'meta.helm.sh/release-name')"),
            text("(annotations ->> 'meta.helm.sh/release-namespace')"),
            postgresql_where=text(
                "deleted_at IS NULL "
                "AND labels ->> 'app.kubernetes.io/managed-by' = 'Helm' "
                "AND annotations ? 'meta.helm.sh/release-name' "
                "AND annotations ? 'meta.helm.sh/release-namespace'"
            ),
        ),
    )

    inventory_key: Mapped[str] = mapped_column(Text, primary_key=True)
    snapshot_id: Mapped[str] = text_column()
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    resource_type: Mapped[str] = text_column()
    api_version: Mapped[str] = text_column()
    kind: Mapped[str] = text_column()
    namespace: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = text_column()
    uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = text_column()
    health: Mapped[str] = text_column()
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = jsonb_column()
    raw: Mapped[dict[str, Any]] = jsonb_column()
    observed_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    first_seen_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_seen_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    deleted_at: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class ClusterUsageSampleRecord(Base):
    __tablename__ = "cluster_usage_samples"
    __table_args__ = (
        Index("ix_cluster_usage_samples_scope", "workspace_id", "cluster_id", "sampled_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = text_column()
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    sampled_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    usage: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
