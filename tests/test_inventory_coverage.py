from __future__ import annotations

from sqlalchemy.dialects import postgresql

from domains.inventory.coverage import (
    InventoryDeleteScope,
    inventory_deletion_scopes,
    inventory_row_in_deletion_scopes,
)
from domains.inventory.kubernetes_snapshot import kubernetes_evidence_to_inventory_snapshot
from domains.inventory.repository import (
    _latest_inventory_snapshots_statement,
    inventory_snapshot_partial_reason_codes,
)
from domains.inventory_filter.query import ResourceFilters
from domains.inventory_filter.repository import (
    _missing_projection_resource_delete_safe,
    _physical_topology_statements,
)


def test_latest_inventory_snapshot_statement_ignores_stale_snapshots() -> None:
    statement = _latest_inventory_snapshots_statement("workspace-a", {"cluster-a"})
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "ignored_stale" in sql


def test_kubernetes_snapshot_records_delete_safe_namespace_coverage() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "collection_scopes": [{"namespace": "target", "label_selector": None}],
            "pods": [{"namespace": "target", "name": "api-1", "phase": "Running"}],
            "services": [{"namespace": "target", "name": "api", "type": "ClusterIP"}],
            "nodes": [{"name": "node-a", "ready": True}],
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    source_summary = snapshot["summary"]
    coverage = source_summary["collection_coverage"]
    pod_coverage = next(item for item in coverage if item["collection"] == "pods")
    node_coverage = next(item for item in coverage if item["collection"] == "nodes")
    ingress_coverage = next(item for item in coverage if item["collection"] == "ingresses")

    assert pod_coverage["observed"] is True
    assert pod_coverage["complete"] is True
    assert pod_coverage["delete_safe"] is True
    assert pod_coverage["scope"] == "namespace"
    assert pod_coverage["namespace"] == "target"
    assert node_coverage["complete"] is True
    assert node_coverage["scope"] == "cluster"
    assert ingress_coverage["observed"] is False
    assert ingress_coverage["delete_safe"] is False
    assert "collection_not_observed" in ingress_coverage["reason_codes"]
    assert snapshot["replace"] is False
    assert source_summary["resources_complete"] is False

    scopes = inventory_deletion_scopes(source_summary)
    assert inventory_row_in_deletion_scopes(
        {"resource_type": "pod", "namespace": "target"},
        scopes,
    )
    assert not inventory_row_in_deletion_scopes(
        {"resource_type": "pod", "namespace": "other"},
        scopes,
    )
    assert not inventory_row_in_deletion_scopes(
        {"resource_type": "ingress", "namespace": "target"},
        scopes,
    )


def test_label_selector_scope_is_not_delete_authoritative() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "collection_scopes": [{"namespace": "target", "label_selector": "app=api"}],
            "pods": [{"namespace": "target", "name": "api-1", "phase": "Running"}],
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    source_summary = snapshot["summary"]
    pod_coverage = next(
        item for item in source_summary["collection_coverage"] if item["collection"] == "pods"
    )

    assert pod_coverage["complete"] is False
    assert pod_coverage["delete_safe"] is False
    assert "label_selector_scope" in pod_coverage["reason_codes"]
    assert inventory_deletion_scopes(source_summary) == ()


def test_truncated_collection_is_not_delete_authoritative() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "collection_scopes": [{"namespace": "target", "label_selector": None}],
            "collection_limits": {
                "truncated": True,
                "lists": {"pods": {"truncated": True, "original_count": 2, "returned_count": 1}},
            },
            "pods": [{"namespace": "target", "name": "api-1", "phase": "Running"}],
            "services": [{"namespace": "target", "name": "api", "type": "ClusterIP"}],
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    source_summary = snapshot["summary"]
    coverage = source_summary["collection_coverage"]
    pod_coverage = next(item for item in coverage if item["collection"] == "pods")
    service_coverage = next(item for item in coverage if item["collection"] == "services")

    assert pod_coverage["complete"] is False
    assert pod_coverage["delete_safe"] is False
    assert "collection_truncated" in pod_coverage["reason_codes"]
    assert service_coverage["complete"] is True

    scopes = inventory_deletion_scopes(source_summary)
    assert not inventory_row_in_deletion_scopes(
        {"resource_type": "pod", "namespace": "target"},
        scopes,
    )
    assert inventory_row_in_deletion_scopes(
        {"resource_type": "service", "namespace": "target"},
        scopes,
    )


def test_empty_scoped_collection_can_prove_delete_scope() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "collection_scopes": [{"namespace": "target", "label_selector": None}],
            "pods": [],
            "services": [],
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    source_summary = snapshot["summary"]
    scopes = inventory_deletion_scopes(source_summary)

    assert source_summary["live_inventory"] is True
    assert inventory_row_in_deletion_scopes(
        {"resource_type": "pod", "namespace": "target"},
        scopes,
    )
    assert inventory_row_in_deletion_scopes(
        {"resource_type": "service", "namespace": "target"},
        scopes,
    )
    assert not inventory_row_in_deletion_scopes(
        {"resource_type": "ingress", "namespace": "target"},
        scopes,
    )


def test_deletion_scopes_ignore_payload_resource_type_claims() -> None:
    scopes = inventory_deletion_scopes(
        {
            "live_inventory": True,
            "collection_coverage": [
                {
                    "collection": "pods",
                    "resource_types": ["node", "secret"],
                    "scope": "namespace",
                    "namespace": "target",
                    "label_selector": None,
                    "observed": True,
                    "complete": True,
                    "delete_safe": True,
                    "truncated": False,
                    "reason_codes": [],
                },
                {
                    "collection": "events",
                    "resource_types": ["pod"],
                    "scope": "namespace",
                    "namespace": "target",
                    "label_selector": None,
                    "observed": True,
                    "complete": True,
                    "delete_safe": True,
                    "truncated": False,
                    "reason_codes": [],
                },
            ],
        }
    )

    assert inventory_row_in_deletion_scopes(
        {"resource_type": "pod", "namespace": "target"},
        scopes,
    )
    assert not inventory_row_in_deletion_scopes(
        {"resource_type": "node", "namespace": "target"},
        scopes,
    )
    assert not inventory_row_in_deletion_scopes(
        {"resource_type": "secret", "namespace": "target"},
        scopes,
    )


def test_deletion_scopes_require_authoritative_coverage_shape() -> None:
    base_entry = {
        "collection": "pods",
        "resource_types": ["pod"],
        "scope": "namespace",
        "namespace": "target",
        "label_selector": None,
        "observed": True,
        "complete": True,
        "delete_safe": True,
        "truncated": False,
        "reason_codes": [],
    }

    for override in (
        {"observed": False},
        {"truncated": True},
        {"label_selector": "app=api"},
        {"reason_codes": ["collection_rbac_denied"]},
        {"scope": "cluster"},
    ):
        scopes = inventory_deletion_scopes(
            {
                "live_inventory": True,
                "collection_coverage": [{**base_entry, **override}],
            }
        )
        assert scopes == ()


def test_collection_status_rbac_denied_is_not_delete_authoritative() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "collection_scopes": [{"namespace": "target", "label_selector": None}],
            "pods": [{"namespace": "target", "name": "api-1", "phase": "Running"}],
            "ingresses": [],
            "collection_status": {
                "ingresses": {
                    "observed": False,
                    "reason_codes": ["collection_rbac_denied"],
                }
            },
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    source_summary = snapshot["summary"]
    ingress_coverage = next(
        item for item in source_summary["collection_coverage"] if item["collection"] == "ingresses"
    )

    assert ingress_coverage["observed"] is False
    assert ingress_coverage["delete_safe"] is False
    assert "collection_rbac_denied" in ingress_coverage["reason_codes"]
    assert not inventory_row_in_deletion_scopes(
        {"resource_type": "ingress", "namespace": "target"},
        inventory_deletion_scopes(source_summary),
    )


def test_empty_no_scope_snapshot_is_not_live_inventory() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "api_resource_discovery": {
                "completeness": "partial",
                "reason_codes": ["api_groups_failed"],
            },
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    source_summary = snapshot["summary"]

    assert source_summary["live_inventory"] is False
    assert "collection_coverage" not in source_summary
    assert inventory_deletion_scopes(source_summary) == ()


def test_legacy_no_scope_standard_resource_remains_live_inventory() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "pods": [{"namespace": "target", "name": "api-1", "phase": "Running"}],
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    assert snapshot["summary"]["live_inventory"] is True


def test_no_scope_dynamic_resource_only_snapshot_is_not_live_inventory() -> None:
    snapshot = kubernetes_evidence_to_inventory_snapshot(
        {
            "cluster": {"collected_at": "2026-07-22T09:00:00+00:00"},
            "custom_resources": [
                {
                    "api_version": "example.io/v1",
                    "kind": "Widget",
                    "namespace": "target",
                    "name": "widget-a",
                    "uid": "uid-a",
                    "resource_version": "1",
                }
            ],
        },
        cluster_id="cluster-a",
        agent_id="agent-a",
    )

    assert snapshot["summary"]["live_inventory"] is False


def test_partial_projection_close_uses_delete_scope() -> None:
    assert _missing_projection_resource_delete_safe(
        {"resource_type": "pod", "namespace": "target"},
        resources_complete=False,
        deletion_scopes=(InventoryDeleteScope("pod", "target"),),
    )
    assert not _missing_projection_resource_delete_safe(
        {"resource_type": "pod", "namespace": "other"},
        resources_complete=False,
        deletion_scopes=(InventoryDeleteScope("pod", "target"),),
    )
    assert _missing_projection_resource_delete_safe(
        {"resource_type": "pod", "namespace": "other"},
        resources_complete=True,
        deletion_scopes=(),
    )


def test_physical_topology_current_nodes_use_latest_resource_rows() -> None:
    filters = ResourceFilters(
        clusters=(),
        namespaces=(),
        applications=(),
        resource_types=(),
        health=(),
        labels=(),
        query=None,
        include_deleted=False,
    )
    server_statement, _, _ = _physical_topology_statements(
        workspace_id="workspace-a",
        cluster_ids=("cluster-a",),
        allowed_application_ids=(),
        filters=filters,
        snapshot_revision=1,
    )
    sql = str(server_statement.compile(compile_kwargs={"literal_binds": True}))

    assert "physical_topology_current_resources" in sql
    assert "physical_topology_current_resources.snapshot_id" in sql
    assert "source_snapshot_id = physical_topology_inventory.as_of_snapshot_id" not in sql


def test_physical_topology_returns_all_active_pods_without_per_node_cap() -> None:
    filters = ResourceFilters(
        clusters=(),
        namespaces=(),
        applications=(),
        resource_types=(),
        health=(),
        labels=(),
        query=None,
        include_deleted=False,
    )
    _, pod_statement, _ = _physical_topology_statements(
        workspace_id="workspace-a",
        cluster_ids=("cluster-a",),
        allowed_application_ids=(),
        filters=filters,
        snapshot_revision=1,
    )
    sql = str(
        pod_statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "placement_rank <=" not in sql
    assert "succeeded" in sql
    assert "failed" in sql


def test_inventory_snapshot_partial_reasons_distinguish_truncation() -> None:
    assert inventory_snapshot_partial_reason_codes(
        {"summary": {"resources_complete": False}}
    ) == ("source_resources_incomplete",)
    assert inventory_snapshot_partial_reason_codes(
        {
            "summary": {
                "resources_complete": False,
                "collection_limits": {"truncated": True},
            }
        }
    ) == ("source_resources_truncated",)
    assert inventory_snapshot_partial_reason_codes(
        {"summary": {"resources_complete": True}}
    ) == ()
