"""변경↔장애 상관 projection과 incident 조회 인덱스.

Revision ID: 20260713_0820
Revises: 20260713_0750
Create Date: 2026-07-13 08:20:00

기존 rca_timeline은 쓰기가 계속되므로 incident 인덱스만 CONCURRENTLY로 생성한다.
빌드 실패 시 INVALID 인덱스가 남을 수 있어 재시도 전에 같은 이름을 제거한다.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_0820"
down_revision: str | None = "20260713_0750"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMELINE_INDEX_NAME = "ix_rca_timeline_workspace_incident"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {TIMELINE_INDEX_NAME}"))
        op.execute(
            sa.text(
                f"""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                    {TIMELINE_INDEX_NAME}
                ON rca_timeline (workspace_id, incident_id)
                WHERE incident_id IS NOT NULL
                """
            )
        )
    op.add_column(
        "audit_log",
        sa.Column("event_created_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_table(
        "workload_changes",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("resource_name", sa.Text(), nullable=False),
        sa.Column("repository_id", sa.Text(), nullable=False),
        sa.Column("binding_id", sa.Text(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=False),
        sa.Column("repo_ref", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("workflow_run_id", sa.Text(), nullable=False),
        sa.Column("image_before", sa.Text(), nullable=True),
        sa.Column("image_after", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(workspace_id) <> ''", name="ck_workload_changes_workspace"),
        sa.CheckConstraint("btrim(cluster_id) <> ''", name="ck_workload_changes_cluster"),
        sa.CheckConstraint("btrim(namespace) <> ''", name="ck_workload_changes_namespace"),
        sa.CheckConstraint(
            "btrim(resource_kind) <> '' AND lower(resource_kind) <> 'unknown'",
            name="ck_workload_changes_resource_kind",
        ),
        sa.CheckConstraint(
            "btrim(resource_name) <> '' AND lower(resource_name) <> 'unknown'",
            name="ck_workload_changes_resource_name",
        ),
        sa.CheckConstraint("btrim(repository_id) <> ''", name="ck_workload_changes_repository"),
        sa.CheckConstraint("btrim(binding_id) <> ''", name="ck_workload_changes_binding"),
        sa.CheckConstraint("btrim(workflow_run_id) <> ''", name="ck_workload_changes_workflow"),
        sa.CheckConstraint("btrim(commit_sha) <> ''", name="ck_workload_changes_commit"),
        sa.CheckConstraint("btrim(manifest_path) <> ''", name="ck_workload_changes_manifest"),
        sa.PrimaryKeyConstraint("event_id", name="pk_workload_changes"),
        sa.UniqueConstraint(
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
    )
    op.create_index(
        "ix_workload_changes_key_changed",
        "workload_changes",
        [
            "workspace_id",
            "cluster_id",
            "namespace",
            "resource_kind",
            "resource_name",
            "changed_at",
            "event_id",
        ],
    )
    op.create_index(
        "ix_workload_changes_workflow",
        "workload_changes",
        [
            "workspace_id",
            "workflow_run_id",
            "repository_id",
            "binding_id",
        ],
    )
    op.create_table(
        "workflow_pr_references",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("repository_id", sa.Text(), nullable=False),
        sa.Column("binding_id", sa.Text(), nullable=False),
        sa.Column("workflow_run_id", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column("pr_url", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(workspace_id) <> ''", name="ck_workflow_pr_refs_workspace"),
        sa.CheckConstraint("btrim(repository_id) <> ''", name="ck_workflow_pr_refs_repository"),
        sa.CheckConstraint("btrim(binding_id) <> ''", name="ck_workflow_pr_refs_binding"),
        sa.CheckConstraint("btrim(workflow_run_id) <> ''", name="ck_workflow_pr_refs_workflow"),
        sa.CheckConstraint("btrim(commit_sha) <> ''", name="ck_workflow_pr_refs_commit"),
        sa.CheckConstraint("btrim(manifest_path) <> ''", name="ck_workflow_pr_refs_manifest"),
        sa.CheckConstraint("btrim(source_event_id) <> ''", name="ck_workflow_pr_refs_source_event"),
        sa.CheckConstraint("btrim(pr_url) <> ''", name="ck_workflow_pr_refs_pr_url"),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "repository_id",
            "binding_id",
            "workflow_run_id",
            "commit_sha",
            "manifest_path",
            name="pk_workflow_pr_references",
        ),
        sa.UniqueConstraint("source_event_id", name="uq_workflow_pr_refs_source_event"),
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {TIMELINE_INDEX_NAME}"))
    op.drop_table("workflow_pr_references")
    op.drop_index("ix_workload_changes_workflow", table_name="workload_changes")
    op.drop_index("ix_workload_changes_key_changed", table_name="workload_changes")
    op.drop_table("workload_changes")
    op.drop_column("audit_log", "event_created_at")
