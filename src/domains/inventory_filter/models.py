"""Immutable temporal projection used by workspace-wide inventory filters."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, PrimaryKeyConstraint, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import Base, created_at_column, jsonb_column, text_column


class InventoryFilterRevision(Base):
    __tablename__ = "inventory_filter_revisions"
    __table_args__ = (
        Index("ix_inventory_filter_revisions_scope", "workspace_id", "revision_id"),
        Index(
            "ix_inventory_filter_revisions_cluster",
            "workspace_id",
            "cluster_id",
            "revision_id",
        ),
        Index(
            "ix_inventory_filter_revisions_change_coverage",
            "workspace_id",
            "cluster_id",
            "change_ledger_epoch",
            "resources_complete",
            "observed_at",
            "revision_id",
        ),
    )

    revision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    observed_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    labels_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    resources_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    application_bindings_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    change_ledger_epoch: Mapped[str | None] = mapped_column(Text, nullable=True)
    partial_reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[Any] = created_at_column()


class InventoryResourceVersion(Base):
    __tablename__ = "inventory_resource_versions"
    __table_args__ = (
        Index(
            "ux_inventory_versions_active_key",
            "workspace_id",
            "inventory_key",
            unique=True,
            postgresql_where=text("valid_to_revision IS NULL"),
        ),
        Index(
            "ix_inventory_versions_history",
            "workspace_id",
            "cluster_id",
            "inventory_key",
            "valid_from_revision",
        ),
        Index(
            "ix_inventory_versions_scope_sort",
            "workspace_id",
            "cluster_id",
            "resource_type",
            "namespace",
            "health",
            "name",
            "inventory_key",
        ),
        Index(
            "ix_inventory_versions_page_sort",
            "workspace_id",
            "cluster_id",
            text("COALESCE(namespace, '')"),
            "resource_type",
            "kind",
            "name",
            "inventory_key",
        ),
        Index(
            "ix_inventory_versions_validity",
            "workspace_id",
            "cluster_id",
            "valid_from_revision",
            "valid_to_revision",
            "inventory_key",
        ),
        Index(
            "ix_inventory_versions_search",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_inventory_versions_active_facets",
            "workspace_id",
            "cluster_id",
            postgresql_include=(
                "version_id",
                "inventory_key",
                "resource_type",
                "kind",
                "namespace",
                "name",
                "health",
                "search_text",
            ),
            postgresql_where=text("valid_to_revision IS NULL"),
        ),
    )

    version_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inventory_key: Mapped[str] = text_column()
    source_snapshot_id: Mapped[str] = text_column()
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    valid_from_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valid_to_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valid_to_observed_at: Mapped[Any | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = text_column()
    resource_type: Mapped[str] = text_column()
    api_version: Mapped[str] = text_column()
    kind: Mapped[str] = text_column()
    namespace: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = text_column()
    uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = text_column()
    health: Mapped[str] = text_column()
    labels: Mapped[dict[str, Any]] = jsonb_column()
    summary: Mapped[dict[str, Any]] = jsonb_column()
    application_binding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    search_text: Mapped[str] = text_column()
    observed_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    first_seen_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[Any] = created_at_column()


class InventoryResourceLabelVersion(Base):
    __tablename__ = "inventory_resource_label_versions"
    __table_args__ = (
        PrimaryKeyConstraint("version_id", "key"),
        Index(
            "ix_inventory_label_versions_facet",
            "workspace_id",
            "cluster_id",
            "key",
            "value",
            "version_id",
        ),
        Index(
            "ix_inventory_label_versions_selector",
            "selector",
            postgresql_using="gin",
            postgresql_ops={"selector": "gin_trgm_ops"},
        ),
        Index(
            "ix_inventory_label_versions_active_lookup",
            "version_id",
            "workspace_id",
            postgresql_include=("selector", "key", "value"),
        ),
    )

    version_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_resource_versions.version_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    key: Mapped[str] = text_column()
    value: Mapped[str] = text_column()
    selector: Mapped[str] = text_column()


class InventoryResourceApplicationVersion(Base):
    __tablename__ = "inventory_resource_application_versions"
    __table_args__ = (
        PrimaryKeyConstraint("version_id", "application_id"),
        Index(
            "ix_inventory_application_versions_lookup",
            "workspace_id",
            "application_id",
            "version_id",
        ),
    )

    version_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_resource_versions.version_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    application_id: Mapped[str] = text_column()
