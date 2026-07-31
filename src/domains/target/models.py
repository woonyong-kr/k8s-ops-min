"""target 도메인 테이블 — agent coordination 상태."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, Integer, PrimaryKeyConstraint, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)
from packages.storage.evidence_predicates import cost_evidence_predicate


class EvidenceWindow(Base):
    __tablename__ = "evidence_windows"
    __table_args__ = (
        Index(
            "ix_evidence_windows_workspace_correlation_cluster",
            "workspace_id",
            "correlation_id",
            "cluster_id",
        ),
        Index(
            "ix_evidence_windows_workspace_cluster_updated",
            "workspace_id",
            "cluster_id",
            "updated_at",
        ),
    )

    evidence_key: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    source_id: Mapped[str] = text_column()
    window_start: Mapped[str] = text_column()
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[str] = text_column()
    correlation_id: Mapped[str] = text_column()
    payload: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


Index(
    "ix_evidence_windows_cost_workspace_cluster_updated",
    EvidenceWindow.workspace_id,
    EvidenceWindow.cluster_id,
    EvidenceWindow.updated_at.desc(),
    EvidenceWindow.evidence_key.desc(),
    postgresql_where=cost_evidence_predicate(EvidenceWindow.payload),
)


class EvidenceJob(Base):
    __tablename__ = "evidence_jobs"
    __table_args__ = (
        Index(
            "ix_evidence_jobs_claim",
            "workspace_id",
            "cluster_id",
            "provider_key",
            "status",
            "created_at",
        ),
        Index("ix_evidence_jobs_window", "evidence_key"),
    )

    job_id: Mapped[str] = mapped_column(Text, primary_key=True)
    evidence_key: Mapped[str] = text_column()
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    source_id: Mapped[str] = text_column()
    provider_key: Mapped[str] = text_column()
    window_start: Mapped[str] = text_column()
    policy_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_policy: Mapped[dict[str, Any]] = jsonb_column()
    status: Mapped[str] = text_column()
    lease_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    leased_until: Mapped[Any | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_policy: Mapped[str] = text_column()
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class TargetDesiredState(Base):
    __tablename__ = "target_desired_states"
    __table_args__ = (PrimaryKeyConstraint("workspace_id", "cluster_id", "component"),)

    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    component: Mapped[str] = text_column()
    namespace: Mapped[str] = text_column()
    version: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class TargetReconcileRecord(Base):
    __tablename__ = "target_reconcile_records"

    reconcile_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    desired_state_version: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    drifted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    message: Mapped[str] = text_column()
    details: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AgentPolicyRecord(Base):
    __tablename__ = "agent_policies"
    __table_args__ = (PrimaryKeyConstraint("workspace_id", "cluster_id"),)

    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    policy: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AgentPolicyStatusRecord(Base):
    __tablename__ = "agent_policy_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = text_column()
    message: Mapped[str] = text_column()
    details: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()


class ClusterAgentStatusRecord(Base):
    __tablename__ = "cluster_agent_status"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "cluster_id", "agent_id"),
        Index("ix_cluster_agent_status_last_seen", "workspace_id", "cluster_id", "last_seen_at"),
    )

    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    agent_id: Mapped[str] = text_column()
    status: Mapped[str] = text_column()
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    details: Mapped[dict[str, Any]] = jsonb_column()
    last_seen_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()


class AgentReconcileStatusRecord(Base):
    __tablename__ = "agent_reconcile_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = text_column()
    message: Mapped[str] = text_column()
    details: Mapped[dict[str, Any]] = jsonb_column()
    created_at: Mapped[Any] = created_at_column()
