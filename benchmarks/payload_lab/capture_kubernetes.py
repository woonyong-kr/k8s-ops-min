from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NAMESPACE = "payload-bench"


def kubectl(*args: str, input_value: dict[str, Any] | None = None) -> str:
    command = ["kubectl", *args]
    result = subprocess.run(
        command,
        input=json.dumps(input_value) if input_value is not None else None,
        text=True,
        check=False,
        capture_output=True,
        env=os.environ,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl failed ({' '.join(command)}): {result.stderr.strip()}")
    return result.stdout


def apply(value: dict[str, Any]) -> None:
    kubectl("apply", "-f", "-", input_value=value)


def list_json(path: str) -> dict[str, Any]:
    return json.loads(kubectl("get", "--raw", path))


def seed_namespace() -> None:
    apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}})
    apply({
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "payload-api", "namespace": NAMESPACE, "labels": {"app": "payload-api"}},
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app": "payload-api"}},
            "template": {
                "metadata": {"labels": {"app": "payload-api"}},
                "spec": {"containers": [{"name": "api", "image": "registry.k8s.io/pause:3.10", "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"memory": "32Mi"}}}]},
            },
        },
    })
    apply({
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "payload-api", "namespace": NAMESPACE},
        "spec": {"selector": {"app": "payload-api"}, "ports": [{"port": 8080, "targetPort": 8080}]},
    })
    now = datetime.now(timezone.utc).isoformat()
    for name, reason, message in (
        ("payload-failed-scheduling", "FailedScheduling", "0/1 nodes are available: Insufficient memory"),
        ("payload-oom-killing", "OOMKilling", "Memory cgroup out of memory: OOMKilled payload-api"),
    ):
        apply({
            "apiVersion": "v1",
            "kind": "Event",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "type": "Warning",
            "reason": reason,
            "message": message,
            "firstTimestamp": now,
            "lastTimestamp": now,
            "count": 1,
            "source": {"component": "payload-lab", "host": "payload-lab-1"},
            "involvedObject": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "payload-api", "namespace": NAMESPACE},
        })


def main() -> None:
    destination = Path(os.environ.get("KUBERNETES_FIXTURE_OUT", ".ecc/benchmarks/payload-experiment/fixtures/kubernetes-actual.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    seed_namespace()
    payload = {
        "status": "success",
        "cluster_id": "payload-lab",
        "namespace": NAMESPACE,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "pods": list_json(f"/api/v1/namespaces/{NAMESPACE}/pods"),
        "events": list_json(f"/api/v1/namespaces/{NAMESPACE}/events"),
        "nodes": list_json("/api/v1/nodes"),
        "deployments": list_json(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments"),
        "statefulsets": list_json(f"/apis/apps/v1/namespaces/{NAMESPACE}/statefulsets"),
        "daemonsets": list_json(f"/apis/apps/v1/namespaces/{NAMESPACE}/daemonsets"),
        "replicasets": list_json(f"/apis/apps/v1/namespaces/{NAMESPACE}/replicasets"),
        "controllerrevisions": list_json(f"/apis/apps/v1/namespaces/{NAMESPACE}/controllerrevisions"),
        "jobs": list_json(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs"),
        "cronjobs": list_json(f"/apis/batch/v1/namespaces/{NAMESPACE}/cronjobs"),
        "services": list_json(f"/api/v1/namespaces/{NAMESPACE}/services"),
        "endpointslices": list_json(f"/apis/discovery.k8s.io/v1/namespaces/{NAMESPACE}/endpointslices"),
        "ingresses": list_json(f"/apis/networking.k8s.io/v1/namespaces/{NAMESPACE}/ingresses"),
        "resourcequotas": list_json(f"/api/v1/namespaces/{NAMESPACE}/resourcequotas"),
    }
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"fixture": str(destination), "bytes": destination.stat().st_size, "namespace": NAMESPACE}))


if __name__ == "__main__":
    main()
