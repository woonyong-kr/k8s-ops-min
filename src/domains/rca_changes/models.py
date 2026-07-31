"""변경↔장애 상관 read model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Index, PrimaryKeyConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from packages.storage.base import (
    Base,
    created_at_column,
    jsonb_column,
    text_column,
    updated_at_column,
)


class WorkloadChange(Base):
    __tablename__ = "workload_changes"
    __table_args__ = (
        Index(
            "ix_workload_changes_key_changed",
            "workspace_id",
            "cluster_id",
            "namespace",
            "resource_kind",
            "resource_name",
            "changed_at",
            "event_id",
        ),
        Index(
            "ix_workload_changes_workflow",
            "workspace_id",
            "workflow_run_id",
            "repository_id",
            "binding_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "repository_id",
            "binding_id",
            "workflow_run_id",
            "commit_sha",
            "manifest_path",
            "cluster_id",
            "namespace",
            "resource_kind",
            "resource_name",
            name="uq_workload_changes_deployment_identity",
        ),
        CheckConstraint("btrim(workspace_id) <> ''", name="ck_workload_changes_workspace"),
        CheckConstraint("btrim(cluster_id) <> ''", name="ck_workload_changes_cluster"),
        CheckConstraint("btrim(namespace) <> ''", name="ck_workload_changes_namespace"),
        CheckConstraint("btrim(repository_id) <> ''", name="ck_workload_changes_repository"),
        CheckConstraint("btrim(binding_id) <> ''", name="ck_workload_changes_binding"),
        CheckConstraint("btrim(workflow_run_id) <> ''", name="ck_workload_changes_workflow"),
        CheckConstraint("btrim(commit_sha) <> ''", name="ck_workload_changes_commit"),
        CheckConstraint("btrim(manifest_path) <> ''", name="ck_workload_changes_manifest"),
        CheckConstraint(
            "btrim(resource_kind) <> '' and lower(resource_kind) <> 'unknown'",
            name="ck_workload_changes_resource_kind",
        ),
        CheckConstraint(
            "btrim(resource_name) <> '' and lower(resource_name) <> 'unknown'",
            name="ck_workload_changes_resource_name",
        ),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = text_column()
    cluster_id: Mapped[str] = text_column()
    namespace: Mapped[str] = text_column()
    resource_kind: Mapped[str] = text_column()
    resource_name: Mapped[str] = text_column()
    repository_id: Mapped[str] = text_column()
    binding_id: Mapped[str] = text_column()
    manifest_path: Mapped[str] = text_column()
    repo_ref: Mapped[str] = text_column()
    commit_sha: Mapped[str] = text_column()
    workflow_run_id: Mapped[str] = text_column()
    image_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_details: Mapped[dict[str, Any]] = jsonb_column()
    changed_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[Any] = created_at_column()


class WorkflowPrReference(Base):
    __tablename__ = "workflow_pr_references"
    __table_args__ = (
        PrimaryKeyConstraint(
            "workspace_id",
            "repository_id",
            "binding_id",
            "workflow_run_id",
            "commit_sha",
            "manifest_path",
        ),
        UniqueConstraint("source_event_id", name="uq_workflow_pr_refs_source_event"),
        CheckConstraint("btrim(workspace_id) <> ''", name="ck_workflow_pr_refs_workspace"),
        CheckConstraint("btrim(repository_id) <> ''", name="ck_workflow_pr_refs_repository"),
        CheckConstraint("btrim(binding_id) <> ''", name="ck_workflow_pr_refs_binding"),
        CheckConstraint("btrim(workflow_run_id) <> ''", name="ck_workflow_pr_refs_workflow"),
        CheckConstraint("btrim(commit_sha) <> ''", name="ck_workflow_pr_refs_commit"),
        CheckConstraint("btrim(manifest_path) <> ''", name="ck_workflow_pr_refs_manifest"),
        CheckConstraint("btrim(source_event_id) <> ''", name="ck_workflow_pr_refs_source_event"),
        CheckConstraint("btrim(pr_url) <> ''", name="ck_workflow_pr_refs_pr_url"),
    )

    workspace_id: Mapped[str] = text_column()
    repository_id: Mapped[str] = text_column()
    binding_id: Mapped[str] = text_column()
    workflow_run_id: Mapped[str] = text_column()
    commit_sha: Mapped[str] = text_column()
    manifest_path: Mapped[str] = text_column()
    source_event_id: Mapped[str] = text_column()
    pr_url: Mapped[str] = text_column()
    observed_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[Any] = created_at_column()
    updated_at: Mapped[Any] = updated_at_column()
