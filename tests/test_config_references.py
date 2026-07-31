from __future__ import annotations

import json
from typing import Any

from domains.inventory.config_references import (
    CONFIG_REFERENCE_DEFAULT_WORKLOAD_LIMIT,
    CONFIG_REFERENCE_MAX_ITEMS,
    CONFIG_REFERENCE_REASONS_TRUNCATED,
    config_reference_list_response,
)
from packages.contracts.gateway import limits as gateway_limits


class FakeInventoryDb:
    def __init__(
        self,
        *,
        snapshot: dict[str, Any] | None,
        deployments: list[dict[str, Any]] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.deployments = deployments or []

    def latest_inventory_snapshot(self, workspace_id: str, cluster_id: str) -> dict[str, Any] | None:
        return self.snapshot

    def list_inventory_resources_by_kind(self, **kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["resource_type"] == "workload"
        assert kwargs["kind"] == "Deployment"
        assert kwargs["include_deleted"] is False
        namespace = kwargs["namespace"]
        if namespace is None:
            return self.deployments
        return [row for row in self.deployments if row.get("namespace") == namespace]


def test_config_reference_default_limit_follows_inventory_default() -> None:
    assert (
        CONFIG_REFERENCE_DEFAULT_WORKLOAD_LIMIT
        == gateway_limits.INVENTORY_RESOURCE_DEFAULT_LIMIT
    )


def test_config_reference_projection_extracts_only_reference_identities() -> None:
    db = FakeInventoryDb(
        snapshot=complete_snapshot(),
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.coverage.availability == "available"
    assert response.coverage.workload_count == 1
    assert response.coverage.projected_reference_count == 4
    by_key = {(item.kind, item.namespace, item.name): item for item in response.items}

    app_config = by_key[("ConfigMap", "apps", "app-config")]
    assert {usage.source for usage in app_config.referenced_by} == {
        "env",
        "volume",
        "volume_mount",
    }
    app_secret = by_key[("Secret", "apps", "app-secret")]
    assert {usage.source for usage in app_secret.referenced_by} == {
        "env",
        "volume",
        "volume_mount",
    }
    assert by_key[("ConfigMap", "apps", "app-env")].referenced_by[0].prefix == "APP_"
    assert by_key[("Secret", "apps", "app-env-secret")].referenced_by[0].optional is True

    payload = response.model_dump(mode="json")
    rendered = json.dumps(payload, sort_keys=True)
    assert "plain-env-value-must-not-leak" not in rendered
    assert "data" not in rendered
    assert "stringData" not in rendered


def test_config_reference_projection_bounds_untrusted_raw_strings() -> None:
    deployment = deployment_with_config_refs()
    template_spec = deployment["raw"]["spec"]["template"]["spec"]
    long_env_name = "E" * 300
    long_key = "K" * 300
    long_mount_path = "/" + ("secret-path/" * 240)
    template_spec["containers"][0]["env"].append(
        {
            "name": long_env_name,
            "valueFrom": {
                "secretKeyRef": {
                    "name": "bounded-secret",
                    "key": long_key,
                }
            },
        }
    )
    template_spec["containers"][0]["volumeMounts"][1]["mountPath"] = long_mount_path
    db = FakeInventoryDb(snapshot=complete_snapshot(), deployments=[deployment])

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    payload = response.model_dump(mode="json")
    rendered = json.dumps(payload, sort_keys=True)
    assert long_env_name not in rendered
    assert long_key not in rendered
    assert long_mount_path not in rendered
    by_key = {(item.kind, item.namespace, item.name): item for item in response.items}
    bounded_secret = by_key[("Secret", "apps", "bounded-secret")]
    bounded_usage = bounded_secret.referenced_by[0]
    assert bounded_usage.env_name is None
    assert bounded_usage.key is None
    app_secret_mount = [
        usage
        for usage in by_key[("Secret", "apps", "app-secret")].referenced_by
        if usage.source == "volume_mount"
    ][0]
    assert app_secret_mount.mount_path is None


def test_config_reference_projection_caps_excessive_reference_count() -> None:
    deployment = deployment_with_config_refs()
    template_spec = deployment["raw"]["spec"]["template"]["spec"]
    template_spec["volumes"] = []
    template_spec["containers"][0]["volumeMounts"] = []
    template_spec["containers"][0]["envFrom"] = []
    template_spec["containers"][0]["env"] = [
        {
            "name": f"CFG_{index}",
            "valueFrom": {
                "configMapKeyRef": {
                    "name": f"cfg-{index}",
                    "key": "setting",
                }
            },
        }
        for index in range(CONFIG_REFERENCE_MAX_ITEMS + 5)
    ]
    db = FakeInventoryDb(snapshot=complete_snapshot(), deployments=[deployment])

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert len(response.items) == CONFIG_REFERENCE_MAX_ITEMS
    assert response.coverage.availability == "partial"
    assert "config_reference_projection_limit_reached" in response.coverage.reason_codes


def test_config_reference_projection_reads_persisted_workload_template_shape() -> None:
    db = FakeInventoryDb(
        snapshot=complete_snapshot(),
        deployments=[persisted_workload_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    by_key = {(item.kind, item.namespace, item.name): item for item in response.items}
    assert ("ConfigMap", "apps", "app-config") in by_key
    assert ("Secret", "apps", "app-secret") in by_key


def test_config_reference_projection_reads_init_container_references() -> None:
    deployment = deployment_with_config_refs()
    template_spec = deployment["raw"]["spec"]["template"]["spec"]
    template_spec["volumes"] = []
    template_spec["containers"][0]["env"] = []
    template_spec["containers"][0]["envFrom"] = []
    template_spec["containers"][0]["volumeMounts"] = []
    template_spec["initContainers"] = [
        {
            "name": "migrate",
            "env": [
                {
                    "name": "MIGRATION_CONFIG",
                    "valueFrom": {
                        "configMapKeyRef": {
                            "name": "migration-config",
                            "key": "dsn",
                        }
                    },
                }
            ],
        }
    ]
    db = FakeInventoryDb(snapshot=complete_snapshot(), deployments=[deployment])

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    by_key = {(item.kind, item.namespace, item.name): item for item in response.items}
    init_config = by_key[("ConfigMap", "apps", "migration-config")]
    assert init_config.referenced_by[0].container_name == "migrate"
    assert init_config.referenced_by[0].source == "env"


def test_config_reference_projection_reports_missing_snapshot() -> None:
    response = config_reference_list_response(
        FakeInventoryDb(snapshot=None),
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.items == []
    assert response.coverage.availability == "unavailable"
    assert response.coverage.reason_codes == ("inventory_snapshot_unavailable",)


def test_config_reference_projection_reports_missing_resource_reader() -> None:
    class SnapshotOnlyDb:
        def latest_inventory_snapshot(
            self,
            workspace_id: str,
            cluster_id: str,
        ) -> dict[str, Any] | None:
            return complete_snapshot()

    response = config_reference_list_response(
        SnapshotOnlyDb(),
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.items == []
    assert response.coverage.availability == "unavailable"
    assert response.coverage.reason_codes == ("inventory_resource_repository_unavailable",)


def test_config_reference_projection_preserves_partial_source_reason() -> None:
    db = FakeInventoryDb(
        snapshot={
            "snapshot_id": "snapshot-a",
            "collected_at": "2026-07-23T05:00:00+00:00",
            "summary": {
                "summary": {
                    "resources_complete": False,
                    "collection_limits": {
                        "truncated": True,
                        "lists": {
                            "workloads": {
                                "truncated": True,
                                "original_count": 2,
                                "returned_count": 1,
                            }
                        },
                    },
                }
            },
        },
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.coverage.availability == "partial"
    assert response.coverage.reason_codes == ("source_resources_truncated",)


def test_config_reference_projection_prefers_workload_coverage() -> None:
    db = FakeInventoryDb(
        snapshot={
            "snapshot_id": "snapshot-a",
            "collected_at": "2026-07-23T05:00:00+00:00",
            "summary": {
                "summary": {
                    "resources_complete": False,
                    "collection_coverage": [
                        {
                            "collection": "workloads",
                            "resource_types": ["workload"],
                            "scope": "namespace",
                            "namespace": "apps",
                            "observed": True,
                            "complete": True,
                            "delete_safe": True,
                            "truncated": False,
                            "reason_codes": [],
                        },
                        {
                            "collection": "ingresses",
                            "resource_types": ["ingress"],
                            "scope": "namespace",
                            "namespace": "apps",
                            "observed": False,
                            "complete": False,
                            "delete_safe": False,
                            "truncated": False,
                            "reason_codes": ["collection_not_observed"],
                        },
                    ],
                }
            },
        },
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.coverage.availability == "available"
    assert response.coverage.reason_codes == ()


def test_config_reference_projection_marks_all_namespace_partial_when_any_workload_scope_incomplete() -> None:
    db = FakeInventoryDb(
        snapshot={
            "snapshot_id": "snapshot-a",
            "collected_at": "2026-07-23T05:00:00+00:00",
            "summary": {
                "summary": {
                    "resources_complete": False,
                    "collection_coverage": [
                        {
                            "collection": "workloads",
                            "scope": "namespace",
                            "namespace": "apps",
                            "complete": True,
                            "reason_codes": [],
                        },
                        {
                            "collection": "workloads",
                            "scope": "namespace",
                            "namespace": "other",
                            "complete": False,
                            "reason_codes": ["collection_truncated"],
                        },
                    ],
                }
            },
        },
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace=None,
    )

    assert response.coverage.availability == "partial"
    assert response.coverage.reason_codes == ("workload_collection_truncated",)


def test_config_reference_projection_normalizes_blank_namespace_to_all_namespaces() -> None:
    db = FakeInventoryDb(
        snapshot=complete_snapshot(),
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace=" ",
    )

    assert response.namespace is None
    assert response.items


def test_config_reference_projection_rejects_oversized_namespace_filter() -> None:
    db = FakeInventoryDb(
        snapshot=complete_snapshot(),
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="n" * (gateway_limits.KUBERNETES_NAME_MAX_LENGTH + 1),
    )

    assert response.namespace is None
    assert response.items == []
    assert response.coverage.availability == "unavailable"
    assert response.coverage.reason_codes == ("invalid_namespace",)


def test_config_reference_projection_marks_uncovered_namespace_partial() -> None:
    db = FakeInventoryDb(
        snapshot={
            "snapshot_id": "snapshot-a",
            "collected_at": "2026-07-23T05:00:00+00:00",
            "summary": {
                "summary": {
                    "resources_complete": True,
                    "collection_coverage": [
                        {
                            "collection": "workloads",
                            "scope": "namespace",
                            "namespace": "other",
                            "complete": True,
                            "reason_codes": [],
                        }
                    ],
                }
            },
        },
        deployments=[],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.items == []
    assert response.coverage.availability == "partial"
    assert response.coverage.reason_codes == ("workload_collection_not_observed",)


def test_config_reference_projection_marks_incomplete_workload_coverage_without_reason() -> None:
    db = FakeInventoryDb(
        snapshot={
            "snapshot_id": "snapshot-a",
            "collected_at": "2026-07-23T05:00:00+00:00",
            "summary": {
                "summary": {
                    "resources_complete": True,
                    "collection_coverage": [
                        {
                            "collection": "workloads",
                            "scope": "namespace",
                            "namespace": "apps",
                            "complete": False,
                            "reason_codes": [],
                        }
                    ],
                }
            },
        },
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.coverage.availability == "partial"
    assert response.coverage.reason_codes == ("workload_collection_incomplete",)


def test_config_reference_projection_bounds_reason_code_count() -> None:
    reason_limit = gateway_limits.CONFIG_REFERENCE_REASON_CODE_MAX_COUNT
    db = FakeInventoryDb(
        snapshot={
            "snapshot_id": "snapshot-a",
            "collected_at": "2026-07-23T05:00:00+00:00",
            "summary": {
                "summary": {
                    "resources_complete": True,
                    "collection_coverage": [
                        {
                            "collection": "workloads",
                            "scope": "namespace",
                            "namespace": "apps",
                            "complete": False,
                            "reason_codes": [
                                f"collection_partial_reason_{index}"
                                for index in range(reason_limit + 3)
                            ],
                        }
                    ],
                }
            },
        },
        deployments=[deployment_with_config_refs()],
    )

    response = config_reference_list_response(
        db,
        workspace_id="default",
        cluster_id="cluster-a",
        namespace="apps",
    )

    assert response.coverage.availability == "partial"
    assert len(response.coverage.reason_codes) == reason_limit
    assert response.coverage.reason_codes[-1] == CONFIG_REFERENCE_REASONS_TRUNCATED


def complete_snapshot() -> dict[str, Any]:
    return {
        "snapshot_id": "snapshot-a",
        "collected_at": "2026-07-23T05:00:00+00:00",
        "summary": {"summary": {"resources_complete": True}},
    }


def persisted_workload_with_config_refs() -> dict[str, Any]:
    return {
        "namespace": "apps",
        "name": "api",
        "uid": "deployment-uid",
        "raw": {
            "kind": "Deployment",
            "namespace": "apps",
            "name": "api",
            "uid": "deployment-uid",
            "pod_template": deployment_with_config_refs()["raw"]["spec"]["template"],
        },
    }


def deployment_with_config_refs() -> dict[str, Any]:
    return {
        "namespace": "apps",
        "name": "api",
        "uid": "deployment-uid",
        "raw": {
            "metadata": {
                "namespace": "apps",
                "name": "api",
                "uid": "deployment-uid",
            },
            "spec": {
                "template": {
                    "spec": {
                        "volumes": [
                            {
                                "name": "config-volume",
                                "configMap": {"name": "app-config", "optional": True},
                            },
                            {
                                "name": "secret-volume",
                                "secret": {"secretName": "app-secret"},
                            },
                        ],
                        "containers": [
                            {
                                "name": "web",
                                "env": [
                                    {
                                        "name": "MODE",
                                        "valueFrom": {
                                            "configMapKeyRef": {
                                                "name": "app-config",
                                                "key": "mode",
                                            }
                                        },
                                    },
                                    {
                                        "name": "TOKEN",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "app-secret",
                                                "key": "token",
                                            }
                                        },
                                    },
                                    {
                                        "name": "PLAIN_SECRET",
                                        "value": "plain-env-value-must-not-leak",
                                    },
                                ],
                                "envFrom": [
                                    {
                                        "prefix": "APP_",
                                        "configMapRef": {"name": "app-env"},
                                    },
                                    {
                                        "secretRef": {
                                            "name": "app-env-secret",
                                            "optional": True,
                                        }
                                    },
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "config-volume",
                                        "mountPath": "/etc/app",
                                    },
                                    {
                                        "name": "secret-volume",
                                        "mountPath": "/var/run/secrets/app",
                                        "readOnly": True,
                                    },
                                ],
                            }
                        ],
                    }
                }
            },
        },
    }
