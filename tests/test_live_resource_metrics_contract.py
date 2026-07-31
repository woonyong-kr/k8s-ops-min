from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import ROOT, load_file
from pydantic import ValidationError

from packages.contracts.realtime import LiveNodeResourceObservation

live_metrics = load_file(
    ROOT / "src" / "services" / "target" / "cluster-agent" / "live_resource_metrics.py",
    "test_live_resource_metrics_module",
)


def test_node_and_cluster_observation_publish_measured_capacity_percentages() -> None:
    observed_at = datetime(2026, 7, 24, 2, 33, 18, tzinfo=UTC)
    collector = live_metrics.NodeClusterResourceMetricsCollector("cluster-a")
    node = collector._node_observation(
        {
            "metadata": {"name": "node-a", "uid": "node-uid-a"},
            "status": {
                "allocatable": {"cpu": "2", "memory": "4Gi"},
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        },
        {
            "nodeName": "node-a",
            "cpu": {
                "time": observed_at.isoformat(),
                "usageNanoCores": 500_000_000,
            },
            "memory": {
                "time": observed_at.isoformat(),
                "workingSetBytes": 1024 * 1024 * 1024,
            },
        },
        reason=None,
        status_reason=None,
        status_observed_at=observed_at,
        tick_observed_at=observed_at,
    )

    assert node.cpu_mcores == pytest.approx(500)
    assert node.mem_mib == pytest.approx(1024)
    assert node.cpu_capacity_mcores == pytest.approx(2000)
    assert node.mem_capacity_mib == pytest.approx(4096)
    assert node.cpu_pct == pytest.approx(25)
    assert node.mem_pct == pytest.approx(25)

    cluster = collector._cluster_observation(
        [node],
        actual_interval_seconds=1,
        collection_complete=True,
        targets_degraded_reason=None,
    )

    assert cluster.collection_complete is True
    assert cluster.status == "ready"
    assert cluster.nodes_ready == 1
    assert cluster.nodes_total == 1
    assert cluster.cpu_capacity_mcores == pytest.approx(2000)
    assert cluster.mem_capacity_mib == pytest.approx(4096)
    assert cluster.cpu_pct == pytest.approx(25)
    assert cluster.mem_pct == pytest.approx(25)


def test_percentage_contract_rejects_values_without_usage_and_capacity_evidence() -> None:
    with pytest.raises(ValidationError, match="percentage requires measured usage and capacity"):
        LiveNodeResourceObservation(
            name="node-a",
            status="unknown",
            cpu_pct=42,
            source="unavailable",
            stale=True,
            status_stale=True,
        )
