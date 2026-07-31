from domains.inventory_filter.physical_topology import build_physical_topology


def test_physical_topology_exposes_pod_resource_limits() -> None:
    result = {
        "servers": [{"inventory_key": "node-1", "name": "node-1", "status": "Ready"}],
        "pods": [
            {
                "inventory_key": "pod-1",
                "name": "api",
                "namespace": "default",
                "status": "Running",
                "health": "healthy",
                "placement_node_name": "node-1",
                "summary": {
                    "node_name": "node-1",
                    "cpu_request_mcores": 100,
                    "cpu_limit_mcores": 500,
                    "mem_request_mib": 64,
                    "mem_limit_mib": 256,
                    "restart_total": 0,
                },
            }
        ],
        "pod_counts_by_node_name": {"node-1": {"matched": 1, "total": 1}},
        "truncated_by_node_name": {},
        "unassigned_truncated_count": 0,
    }
    usage_sample = {
        "sampled_at": "2026-07-23T00:00:00Z",
        "usage": {
            "nodes": {},
            "pods": {"default/api": {"cpu_mcores": 250, "mem_mib": 104}},
        },
    }

    topology = build_physical_topology(
        result,
        latest_usage_sample=usage_sample,
        matched_count_completeness="exact",
        total_count_completeness="exact",
    )

    assert topology["pods"][0]["cpu_limit_mcores"] == 500
    assert topology["pods"][0]["mem_limit_mib"] == 256
