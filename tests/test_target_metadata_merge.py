"""Metadata evidence from multiple controlled namespaces remains one lossless bucket."""

from __future__ import annotations

import sys
from typing import Any

from conftest import ROOT, load_file


def _metadata_provider_module() -> Any:
    agent_root = ROOT / "src" / "services" / "target" / "cluster-agent"
    sys.path.insert(0, str(agent_root))
    try:
        return load_file(
            agent_root / "providers" / "metadata_providers.py",
            "test_control_scope_metadata_providers",
        )
    finally:
        sys.path.remove(str(agent_root))


def _resource(namespace: str, name: str) -> dict[str, str]:
    return {"kind": "Deployment", "namespace": namespace, "name": name}


def test_merge_change_context_accumulates_namespace_lists_without_duplicates() -> None:
    metadata = _metadata_provider_module()
    target = {
        metadata.CURRENT_WORKLOAD_SNAPSHOTS_KEY: [
            {"workload": _resource("target", "cluster-agent")},
        ],
        metadata.SERVICE_SELECTOR_MATCHES_KEY: [
            {"service": {"namespace": "target", "name": "cluster-agent"}},
        ],
        metadata.ENDPOINT_SLICE_READY_ENDPOINTS_KEY: [
            {
                "endpoint_slice": {
                    "namespace": "target",
                    "name": "cluster-agent-1",
                },
            },
        ],
        metadata.REFERENCED_CONFIG_OBJECTS_KEY: [
            {"kind": "ConfigMap", "namespace": "target", "name": "runtime"},
        ],
        metadata.RESOURCE_QUOTAS_KEY: [
            {"namespace": "target", "name": "target-quota"},
        ],
    }
    source = {
        metadata.CURRENT_WORKLOAD_SNAPSHOTS_KEY: [
            {"workload": _resource("sandbox", "lobby")},
        ],
        metadata.SERVICE_SELECTOR_MATCHES_KEY: [
            {"service": {"namespace": "sandbox", "name": "lobby"}},
        ],
        metadata.ENDPOINT_SLICE_READY_ENDPOINTS_KEY: [
            {
                "endpoint_slice": {
                    "namespace": "sandbox",
                    "name": "lobby-1",
                },
            },
        ],
        metadata.REFERENCED_CONFIG_OBJECTS_KEY: [
            {"kind": "ConfigMap", "namespace": "sandbox", "name": "lobby"},
        ],
        metadata.RESOURCE_QUOTAS_KEY: [
            {"namespace": "sandbox", "name": "sandbox-quota"},
        ],
    }

    metadata.merge_change_context(target, source)
    metadata.merge_change_context(target, source)

    assert [
        item["workload"]["namespace"]
        for item in target[metadata.CURRENT_WORKLOAD_SNAPSHOTS_KEY]
    ] == ["target", "sandbox"]
    assert [
        item["service"]["namespace"]
        for item in target[metadata.SERVICE_SELECTOR_MATCHES_KEY]
    ] == ["target", "sandbox"]
    assert [
        item["endpoint_slice"]["namespace"]
        for item in target[metadata.ENDPOINT_SLICE_READY_ENDPOINTS_KEY]
    ] == ["target", "sandbox"]
    assert [
        item["namespace"] for item in target[metadata.REFERENCED_CONFIG_OBJECTS_KEY]
    ] == ["target", "sandbox"]
    assert [item["namespace"] for item in target[metadata.RESOURCE_QUOTAS_KEY]] == [
        "target",
        "sandbox",
    ]


def test_build_response_bounds_lists_after_all_namespace_results_are_merged() -> None:
    metadata = _metadata_provider_module()
    provider = metadata.MetadataProvider(cluster_id="c-1")
    snapshots = [
        {"workload": _resource("sandbox", f"workload-{index}")}
        for index in range(metadata.MAX_CURRENT_WORKLOAD_SNAPSHOTS + 1)
    ]
    results = {
        metadata.CHANGE_CONTEXT_KEY: {
            metadata.CURRENT_WORKLOAD_SNAPSHOTS_KEY: snapshots,
        },
    }

    response = provider.build_response(results)

    context = response[metadata.CHANGE_CONTEXT_KEY]
    assert len(context[metadata.CURRENT_WORKLOAD_SNAPSHOTS_KEY]) == (
        metadata.MAX_CURRENT_WORKLOAD_SNAPSHOTS
    )
    assert context[metadata.COLLECTION_LIMITS_KEY]["lists"][
        metadata.CURRENT_WORKLOAD_SNAPSHOTS_KEY
    ] == {
        "truncated": True,
        "original_count": metadata.MAX_CURRENT_WORKLOAD_SNAPSHOTS + 1,
        "returned_count": metadata.MAX_CURRENT_WORKLOAD_SNAPSHOTS,
    }
