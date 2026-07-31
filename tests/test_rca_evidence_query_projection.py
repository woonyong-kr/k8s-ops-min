from domains.rca.query_router import evidence_record


def test_evidence_record_exposes_metadata_window_lineage_for_legacy_links() -> None:
    record = evidence_record(
        {
            "id": 1,
            "workspace_id": "default",
            "correlation_id": "correlation-1",
            "kind": "rca_bundle",
            "payload": {
                "cluster_id": "target-1",
                "metadata": {
                    "change_context": {
                        "current_workload_snapshots": [
                            {
                                "workload": {
                                    "kind": "Deployment",
                                    "namespace": "sandbox",
                                    "name": "checkout",
                                }
                            }
                        ]
                    },
                    "_lineage": {
                        "schema_version": 1,
                        "source_version": "metadata-v2",
                        "collector": "cluster-agent",
                        "collector_version": "2",
                        "query_version": "1",
                        "collected_at": "2026-07-24T00:01:00Z",
                        "evidence_key": "workspace:target:metadata:window-1",
                        "source_id": "metadata",
                        "agent_id": "agent-1",
                        "window_start": "2026-07-24T00:00:30Z",
                    },
                },
            },
            "created_at": "2026-07-24T00:01:01Z",
        }
    )

    assert record["sources"] == [
        {
            "source": "metadata",
            "summary": "metadata evidence",
            "schema_version": 1,
            "source_version": "metadata-v2",
            "collector": "cluster-agent",
            "collector_version": "2",
            "query_version": "1",
            "collected_at": "2026-07-24T00:01:00Z",
            "evidence_key": "workspace:target:metadata:window-1",
            "source_id": "metadata",
            "agent_id": "agent-1",
            "window_start": "2026-07-24T00:00:30Z",
        }
    ]
