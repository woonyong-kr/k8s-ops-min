from __future__ import annotations

from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.target import NODE_COLLECTOR_SERVICE_ACCOUNT_NAME


def node_collector_daemonset(
    *,
    name: str,
    namespace: str,
    image: str,
    app_label: str,
    managed_by_label: str,
    managed_by_value: str,
    container_name: str,
    port: int,
    collect_interval_seconds: int,
) -> JsonObject:
    labels = {
        "app": app_label,
        managed_by_label: managed_by_value,
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {"matchLabels": {"app": app_label}},
            "updateStrategy": {"type": "RollingUpdate"},
            "template": {
                "metadata": {
                    "annotations": {
                        "prometheus.io/path": "/metrics",
                        "prometheus.io/port": str(port),
                        "prometheus.io/scrape": "true",
                    },
                    "labels": labels,
                },
                "spec": {
                    "serviceAccountName": NODE_COLLECTOR_SERVICE_ACCOUNT_NAME,
                    "tolerations": [{"operator": "Exists"}],
                    "containers": [
                        node_collector_container(
                            image=image,
                            container_name=container_name,
                            port=port,
                            collect_interval_seconds=collect_interval_seconds,
                        )
                    ],
                },
            },
        },
    }


def node_collector_container(
    *,
    image: str,
    container_name: str,
    port: int,
    collect_interval_seconds: int,
) -> JsonObject:
    return {
        "name": container_name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "src/services/target/node-collector/app.py"],
        "env": [
            {"name": "PORT", "value": str(port)},
            {
                "name": "COLLECT_INTERVAL_SECONDS",
                "value": str(collect_interval_seconds),
            },
            {"name": "NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}},
            {"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
            {
                "name": "POD_NAMESPACE",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}},
            },
        ],
        "ports": [{"name": "metrics", "containerPort": port}],
    }
