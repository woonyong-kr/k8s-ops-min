"""Workspace-scoped Helm chart source registry tables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import Base, created_at_column, text_column, updated_at_column


class HelmChartSourceRecord(Base):
    __tablename__ = "helm_chart_sources"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "canonical_ref",
            name="uq_helm_chart_sources_workspace_provider_ref",
        ),
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_helm_chart_sources_workspace_name",
        ),
        CheckConstraint(
            "provider IN ('repository', 'oci')",
            name="ck_helm_chart_sources_provider",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_helm_chart_sources_status",
        ),
        Index(
            "ix_helm_chart_sources_workspace_updated",
            "workspace_id",
            "updated_at",
            "source_id",
        ),
    )

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.workspace_id"))
    provider: Mapped[str] = text_column()
    name: Mapped[str] = text_column()
    canonical_ref: Mapped[str] = text_column()
    credential_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = text_column()
    access_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()
