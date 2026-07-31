"""Workspace-wide inventory filter temporal projection.

Revision ID: 20260713_2215
Revises: 20260713_0820
Create Date: 2026-07-13 22:15:00

The baseline copies only currently active inventory rows. Legacy snapshots did not
persist the full-resource replacement bit, so the migration never claims resource
completeness. Label completeness is exact only when the source snapshot explicitly
recorded it. These new projection tables have no writers before the revision lands,
so indexes are built in the same transaction as the schema and baseline; a failure
rolls the revision back and is safe to retry. Substring search requires PostgreSQL
``pg_trgm`` and fails closed when the database role cannot install the extension.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260713_2215"
down_revision: str | None = "20260713_0820"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_DDLS = (
    (
        "ix_inventory_filter_revisions_scope",
        """
        CREATE INDEX ix_inventory_filter_revisions_scope
        ON inventory_filter_revisions (workspace_id, revision_id)
        """,
    ),
    (
        "ix_inventory_filter_revisions_cluster",
        """
        CREATE INDEX ix_inventory_filter_revisions_cluster
        ON inventory_filter_revisions (workspace_id, cluster_id, revision_id)
        """,
    ),
    (
        "ux_inventory_versions_active_key",
        """
        CREATE UNIQUE INDEX ux_inventory_versions_active_key
        ON inventory_resource_versions (workspace_id, inventory_key)
        WHERE valid_to_revision IS NULL
        """,
    ),
    (
        "ix_inventory_versions_history",
        """
        CREATE INDEX ix_inventory_versions_history
        ON inventory_resource_versions
            (workspace_id, cluster_id, inventory_key, valid_from_revision)
        """,
    ),
    (
        "ix_inventory_versions_scope_sort",
        """
        CREATE INDEX ix_inventory_versions_scope_sort
        ON inventory_resource_versions
            (workspace_id, cluster_id, resource_type, namespace, health, name, inventory_key)
        """,
    ),
    (
        "ix_inventory_versions_page_sort",
        """
        CREATE INDEX ix_inventory_versions_page_sort
        ON inventory_resource_versions
            (
                workspace_id,
                cluster_id,
                COALESCE(namespace, ''),
                resource_type,
                kind,
                name,
                inventory_key
            )
        """,
    ),
    (
        "ix_inventory_versions_validity",
        """
        CREATE INDEX ix_inventory_versions_validity
        ON inventory_resource_versions
            (workspace_id, cluster_id, valid_from_revision, valid_to_revision, inventory_key)
        """,
    ),
    (
        "ix_inventory_versions_search",
        """
        CREATE INDEX ix_inventory_versions_search
        ON inventory_resource_versions USING gin (search_text gin_trgm_ops)
        """,
    ),
    (
        "ix_inventory_label_versions_facet",
        """
        CREATE INDEX ix_inventory_label_versions_facet
        ON inventory_resource_label_versions
            (workspace_id, cluster_id, key, value, version_id)
        """,
    ),
    (
        "ix_inventory_label_versions_selector",
        """
        CREATE INDEX ix_inventory_label_versions_selector
        ON inventory_resource_label_versions USING gin (selector gin_trgm_ops)
        """,
    ),
    (
        "ix_inventory_application_versions_lookup",
        """
        CREATE INDEX ix_inventory_application_versions_lookup
        ON inventory_resource_application_versions (workspace_id, application_id, version_id)
        """,
    ),
)

BASELINE_SQL = """
WITH latest_snapshots AS (
    SELECT DISTINCT ON (workspace_id, cluster_id)
        snapshot_id,
        workspace_id,
        cluster_id,
        collected_at,
        summary,
        created_at
    FROM cluster_inventory_snapshots
    ORDER BY
        workspace_id,
        cluster_id,
        collected_at DESC,
        created_at DESC,
        snapshot_id DESC
),
baseline_revisions AS (
    INSERT INTO inventory_filter_revisions (
        snapshot_id,
        workspace_id,
        cluster_id,
        observed_at,
        labels_complete,
        resources_complete,
        application_bindings_complete,
        partial_reason_codes,
        created_at
    )
    SELECT
        snapshot_id,
        workspace_id,
        cluster_id,
        collected_at,
        COALESCE(
            summary -> 'summary' -> 'labels_complete' = 'true'::jsonb,
            FALSE
        ),
        FALSE,
        FALSE,
        CASE
            WHEN summary -> 'summary' -> 'labels_complete' = 'true'::jsonb
            THEN jsonb_build_array(
                'migration_baseline',
                'resources_completeness_unknown',
                'application_bindings_completeness_unknown'
            )
            ELSE jsonb_build_array(
                'migration_baseline',
                'resources_completeness_unknown',
                'labels_completeness_unknown',
                'application_bindings_completeness_unknown'
            )
        END,
        created_at
    FROM latest_snapshots
    RETURNING revision_id, workspace_id, cluster_id
),
latest_successful_runs AS (
    SELECT
        run.workspace_id,
        run.binding_id,
        run.application_id,
        run.cluster_id,
        run.commit_sha,
        row_number() OVER (
            PARTITION BY run.workspace_id, run.binding_id
            ORDER BY run.updated_at DESC, run.workflow_run_id DESC
        ) AS rank
    FROM workflow_runs AS run
    WHERE run.status = 'succeeded'
),
authoritative_application_resources AS (
    SELECT
        application.application_id,
        binding.workspace_id,
        binding.cluster_id,
        artifact.rendered_manifest ->> 'apiVersion' AS api_version,
        lower(artifact.rendered_manifest ->> 'kind') AS kind,
        COALESCE(
            artifact.rendered_manifest -> 'metadata' ->> 'namespace',
            binding.namespace
        ) AS namespace,
        artifact.rendered_manifest -> 'metadata' ->> 'name' AS name
    FROM latest_successful_runs AS run
    JOIN deployment_bindings AS binding
      ON binding.workspace_id = run.workspace_id
     AND binding.binding_id = run.binding_id
     AND binding.cluster_id = run.cluster_id
    JOIN applications AS application
      ON application.workspace_id = binding.workspace_id
     AND application.repository_id = binding.repository_id
     AND application.name = binding.app_name
     AND application.application_id = run.application_id
    JOIN manifest_artifacts AS artifact
      ON artifact.workspace_id = run.workspace_id
     AND artifact.binding_id = run.binding_id
     AND artifact.commit_sha = run.commit_sha
    WHERE run.rank = 1
      AND binding.status = 'active'
      AND artifact.status = 'rendered'
      AND artifact.rendered_manifest IS NOT NULL
      AND jsonb_typeof(artifact.rendered_manifest) = 'object'
),
baseline_versions AS (
    INSERT INTO inventory_resource_versions (
        inventory_key,
        source_snapshot_id,
        workspace_id,
        cluster_id,
        valid_from_revision,
        valid_to_revision,
        valid_to_observed_at,
        content_hash,
        resource_type,
        api_version,
        kind,
        namespace,
        name,
        uid,
        resource_version,
        status,
        health,
        labels,
        summary,
        application_binding_complete,
        search_text,
        observed_at,
        first_seen_at,
        created_at
    )
    SELECT
        resource.inventory_key,
        resource.snapshot_id,
        resource.workspace_id,
        resource.cluster_id,
        baseline.revision_id,
        NULL,
        NULL,
        md5(
            jsonb_build_object(
                'resource_type', resource.resource_type,
                'api_version', resource.api_version,
                'kind', resource.kind,
                'namespace', resource.namespace,
                'name', resource.name,
                'uid', resource.uid,
                'resource_version', resource.resource_version,
                'status', resource.status,
                'health', resource.health,
                'labels', resource.labels,
                'summary', resource.summary,
                'application_ids', application_binding.application_ids
            )::text
        ),
        resource.resource_type,
        resource.api_version,
        resource.kind,
        resource.namespace,
        resource.name,
        resource.uid,
        resource.resource_version,
        resource.status,
        resource.health,
        resource.labels,
        resource.summary,
        FALSE,
        lower(
            concat_ws(
                ' ',
                resource.name,
                resource.kind,
                COALESCE(resource.namespace, ''),
                resource.resource_type
            )
        ),
        resource.observed_at,
        resource.first_seen_at,
        resource.created_at
    FROM cluster_inventory_resources AS resource
    JOIN baseline_revisions AS baseline
     ON baseline.workspace_id = resource.workspace_id
     AND baseline.cluster_id = resource.cluster_id
    CROSS JOIN LATERAL (
        SELECT COALESCE(
            jsonb_agg(
                DISTINCT application.application_id
                ORDER BY application.application_id
            ),
            '[]'::jsonb
        ) AS application_ids
        FROM authoritative_application_resources AS application
        WHERE application.workspace_id = resource.workspace_id
          AND application.cluster_id = resource.cluster_id
          AND application.api_version = resource.api_version
          AND application.kind = lower(resource.kind)
          AND application.namespace IS NOT DISTINCT FROM resource.namespace
          AND application.name = resource.name
    ) AS application_binding
    WHERE resource.deleted_at IS NULL
    RETURNING
        version_id,
        workspace_id,
        cluster_id,
        api_version,
        kind,
        namespace,
        name,
        labels
),
latest_application_bindings AS (
    SELECT DISTINCT
        version.version_id,
        version.workspace_id,
        version.cluster_id,
        application.application_id
    FROM baseline_versions AS version
    JOIN authoritative_application_resources AS application
      ON application.workspace_id = version.workspace_id
     AND application.cluster_id = version.cluster_id
     AND application.api_version = version.api_version
     AND application.kind = lower(version.kind)
     AND application.namespace IS NOT DISTINCT FROM version.namespace
     AND application.name = version.name
),
baseline_applications AS (
    INSERT INTO inventory_resource_application_versions (
        version_id,
        workspace_id,
        cluster_id,
        application_id
    )
    SELECT
        version_id,
        workspace_id,
        cluster_id,
        application_id
    FROM latest_application_bindings
    RETURNING version_id
)
INSERT INTO inventory_resource_label_versions (
    version_id,
    workspace_id,
    cluster_id,
    key,
    value,
    selector
)
SELECT
    version.version_id,
    version.workspace_id,
    version.cluster_id,
    label.label_key,
    label.label_value,
    lower(label.label_key || '=' || label.label_value)
FROM baseline_versions AS version
CROSS JOIN LATERAL jsonb_each_text(
    CASE
        WHEN jsonb_typeof(version.labels) = 'object' THEN version.labels
        ELSE '{}'::jsonb
    END
) AS label(label_key, label_value)
"""


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.create_table(
        "inventory_filter_revisions",
        sa.Column("revision_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("labels_complete", sa.Boolean(), nullable=False),
        sa.Column("resources_complete", sa.Boolean(), nullable=False),
        sa.Column("application_bindings_complete", sa.Boolean(), nullable=False),
        sa.Column(
            "partial_reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint("snapshot_id"),
    )
    op.create_table(
        "inventory_resource_versions",
        sa.Column("version_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("inventory_key", sa.Text(), nullable=False),
        sa.Column("source_snapshot_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("valid_from_revision", sa.BigInteger(), nullable=False),
        sa.Column("valid_to_revision", sa.BigInteger(), nullable=True),
        sa.Column("valid_to_observed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("api_version", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=True),
        sa.Column("resource_version", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("health", sa.Text(), nullable=False),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("application_binding_complete", sa.Boolean(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("version_id"),
    )
    op.create_table(
        "inventory_resource_label_versions",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("selector", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["inventory_resource_versions.version_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "key"),
    )
    op.create_table(
        "inventory_resource_application_versions",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("application_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["inventory_resource_versions.version_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "application_id"),
    )

    op.execute(sa.text(BASELINE_SQL))

    for _, ddl in INDEX_DDLS:
        op.execute(sa.text(ddl))


def downgrade() -> None:
    op.drop_table("inventory_resource_application_versions")
    op.drop_table("inventory_resource_label_versions")
    op.drop_table("inventory_resource_versions")
    op.drop_table("inventory_filter_revisions")
