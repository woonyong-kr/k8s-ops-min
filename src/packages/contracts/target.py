from __future__ import annotations

import re
from enum import StrEnum


class TargetComponent(StrEnum):
    CLUSTER_AGENT = "cluster-agent"
    NODE_COLLECTOR = "node-collector"


class TargetDesiredStateStatus(StrEnum):
    ACTIVE = "active"


class TargetReconcileStatus(StrEnum):
    REQUESTED = "requested"
    IN_SYNC = "in_sync"
    DRIFTED = "drifted"
    FAILED = "failed"


TARGET_NAMESPACE = "target"
SANDBOX_NAMESPACE = "sandbox"
NODE_COLLECTOR_SERVICE_ACCOUNT_NAME = "cluster-agent-node-collector"
NODE_COLLECTOR_READ_CLUSTER_ROLE_NAME = "cluster-agent-node-collector-read"
NODE_COLLECTOR_READ_CLUSTER_ROLE_BINDING_NAME = NODE_COLLECTOR_READ_CLUSTER_ROLE_NAME
CONTROL_PRIORITY_CLASS_NAME = "gitops-control-critical"
FAST_LANE_PRIORITY_CLASS_NAME = "gitops-fast-lane"
FAST_LANE_NODE_LABEL_KEY = "workload-tier"
FAST_LANE_NODE_LABEL_VALUE = "fast-lane"
TARGET_RBAC_MANIFEST_VERSION = "2026-07-18.2"
TARGET_RBAC_VERSION_ANNOTATION = "opsia.dev/target-rbac-version"
TARGET_RUNTIME_CONFIG_NAME = "target-runtime-config"
TARGET_AGENT_IMAGE_KEY = "TARGET_AGENT_IMAGE"
NODE_COLLECTOR_IMAGE_KEY = "NODE_COLLECTOR_IMAGE"
TARGET_OTEL_TRACES_ENDPOINT = "http://opentelemetry-collector.target.svc:4318/v1/traces"
TARGET_RUNTIME_IMAGE_ANNOTATION = "opsia.dev/runtime-image"
TARGET_IMAGE_DIGEST_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}$")


def require_target_image_digest(image: str) -> str:
    candidate = image.strip()
    if not TARGET_IMAGE_DIGEST_PATTERN.fullmatch(candidate):
        raise ValueError("target image must use an immutable sha256 digest")
    return candidate


# Kubernetes evidence query contract. Namespace snapshots remain the default;
# the cluster-events scope is an explicit, separately authorized all-namespace
# Event collection and must never be inferred from a namespace query.
KUBERNETES_QUERY_SCOPE_NAMESPACE = "namespace"
KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS = "cluster_events"
KUBERNETES_QUERY_SCOPE_CLUSTER_DISCOVERY = "cluster_discovery"
KUBERNETES_QUERY_SCOPE_CLUSTER_ACCESS = "cluster_access"
KUBERNETES_QUERY_SCOPE_DYNAMIC_RESOURCE = "dynamic_resource"
KUBERNETES_ALL_NAMESPACES_QUERY = "*"
