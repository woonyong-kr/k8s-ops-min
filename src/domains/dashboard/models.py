"""dashboard 도메인 테이블 — 화면용 read model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class MetricQueryPreset(Base):
    __tablename__ = "metric_query_presets"
    __table_args__ = (UniqueConstraint("workspace_id", "cluster_id", "name"),)

    preset_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.workspace_id"))
    cluster_id: Mapped[str] = text_column()
    name: Mapped[str] = text_column()
    description: Mapped[str] = text_column()
    source: Mapped[str] = text_column()
    query: Mapped[str] = text_column()
    range_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    step_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unit: Mapped[str] = text_column()
    created_by: Mapped[str] = text_column()
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class MetricWidget(Base):
    __tablename__ = "metric_widgets"
    __table_args__ = (UniqueConstraint("workspace_id", "cluster_id", "title"),)

    widget_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.workspace_id"))
    cluster_id: Mapped[str] = text_column()
    query_preset_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("metric_query_presets.preset_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = text_column()
    kind: Mapped[str] = text_column()
    position: Mapped[dict[str, Any]] = jsonb_column()
    settings: Mapped[dict[str, Any]] = jsonb_column()
    created_by: Mapped[str] = text_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class RcaTimeline(Base):
    __tablename__ = "rca_timeline"
    __table_args__ = (
        UniqueConstraint("workspace_id", "correlation_id"),
        Index("ix_rca_timeline_scope_updated", "workspace_id", "updated_at"),
        Index(
            "ix_rca_timeline_open_cluster", "workspace_id", "cluster_id", "status", "incident_id"
        ),
        Index(
            "ix_rca_timeline_occurrence",
            "workspace_id",
            "incident_occurrence_id",
            "updated_at",
            postgresql_where=text("incident_occurrence_id is not null"),
        ),
        Index(
            "ix_rca_timeline_workspace_incident",
            "workspace_id",
            "incident_id",
            postgresql_where=text("incident_id is not null"),
        ),
        Index("ix_rca_timeline_issue_page", "workspace_id", "updated_at", "id"),
        Index(
            "ix_rca_timeline_issue_identity_latest",
            "workspace_id",
            "cluster_id",
            "incident_id",
            "updated_at",
            "id",
        ),
        Index(
            "ix_rca_timeline_issue_severity",
            "workspace_id",
            "severity",
            "updated_at",
            "id",
        ),
        Index(
            "ix_rca_timeline_issue_category",
            "workspace_id",
            "category",
            "updated_at",
            "id",
            postgresql_where=text("category_complete is true"),
        ),
        Index(
            "ix_rca_timeline_issue_environment",
            "workspace_id",
            "environment",
            "updated_at",
            "id",
        ),
        Index(
            "ix_rca_timeline_issue_applications",
            "application_ids",
            postgresql_using="gin",
        ),
        Index(
            "ix_rca_timeline_issue_labels",
            "labels",
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    cluster_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_namespace: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_resource_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_resource_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_symptom: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_logical_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_occurrence_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    environment: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    labels: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)
    severity_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    category_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    environment_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    application_ids_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    labels_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_subject: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    supporting_evidence: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    missing_evidence: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    action_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_id: Mapped[str] = text_column()
    last_event_at: Mapped[str] = text_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()
