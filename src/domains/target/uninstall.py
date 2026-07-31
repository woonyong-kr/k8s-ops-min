"""Target agent uninstall contract.

The management plane has no inbound route to a target cluster.  Disconnect is
therefore an agent-owned operation: the command remains queued while an agent
is offline and registration is revoked only after the authenticated agent has
reported a durable cleanup completion receipt.

Every resource below is an exact name emitted by ``target_install_manifest``.
Namespaces and sample/user workloads are deliberately outside this allowlist.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from domains.command.policy import (
    DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_COMMAND_LEASE_SECONDS,
    DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
    DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
)
from packages.config.constants import Command, CommandStatus, RiskLevel
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.target import (
    NODE_COLLECTOR_READ_CLUSTER_ROLE_BINDING_NAME,
    NODE_COLLECTOR_READ_CLUSTER_ROLE_NAME,
    NODE_COLLECTOR_SERVICE_ACCOUNT_NAME,
    SANDBOX_NAMESPACE,
    TARGET_NAMESPACE,
)

UNINSTALL_CONTRACT_VERSION = 2
UNINSTALL_PRIORITY = 1_000
UNINSTALL_COMMAND_REFERENCE = Command.CLUSTER_AGENT_UNINSTALL_ACTION


@dataclass(frozen=True)
class NamespacedCleanupResource:
    api_group: str
    version: str
    namespace: str
    resource: str
    name: str


@dataclass(frozen=True)
class ClusterCleanupResource:
    api_group: str
    version: str
    resource: str
    name: str


PRE_ACK_NAMESPACED_CLEANUP = (
    NamespacedCleanupResource(
        "apps", "v1", TARGET_NAMESPACE, "daemonsets", "optional-node-collector"
    ),
    NamespacedCleanupResource(
        "core", "v1", TARGET_NAMESPACE, "configmaps", "target-runtime-config"
    ),
    NamespacedCleanupResource("core", "v1", TARGET_NAMESPACE, "configmaps", "target-agent-policy"),
    NamespacedCleanupResource("core", "v1", TARGET_NAMESPACE, "secrets", "target-runtime-secret"),
    NamespacedCleanupResource(
        "core",
        "v1",
        TARGET_NAMESPACE,
        "serviceaccounts",
        NODE_COLLECTOR_SERVICE_ACCOUNT_NAME,
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        TARGET_NAMESPACE,
        "rolebindings",
        "cluster-agent-self-manage",
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        TARGET_NAMESPACE,
        "rolebindings",
        "cluster-agent-target-manage",
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io", "v1", TARGET_NAMESPACE, "roles", "cluster-agent-self-manage"
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io", "v1", TARGET_NAMESPACE, "roles", "cluster-agent-target-manage"
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        SANDBOX_NAMESPACE,
        "rolebindings",
        "cluster-agent-sandbox-write",
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        SANDBOX_NAMESPACE,
        "rolebindings",
        "cluster-agent-catalog-install",
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io", "v1", SANDBOX_NAMESPACE, "roles", "cluster-agent-sandbox-write"
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        SANDBOX_NAMESPACE,
        "rolebindings",
        "cluster-agent-cronjob-control",
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        SANDBOX_NAMESPACE,
        "roles",
        "cluster-agent-cronjob-control",
    ),
    NamespacedCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        SANDBOX_NAMESPACE,
        "roles",
        "cluster-agent-catalog-install",
    ),
)
PRE_ACK_CLUSTER_CLEANUP = (
    ClusterCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        "clusterrolebindings",
        NODE_COLLECTOR_READ_CLUSTER_ROLE_BINDING_NAME,
    ),
    ClusterCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        "clusterroles",
        NODE_COLLECTOR_READ_CLUSTER_ROLE_NAME,
    ),
    ClusterCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        "clusterrolebindings",
        "cluster-agent-gitops-control",
    ),
    ClusterCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        "clusterroles",
        "cluster-agent-gitops-control",
    ),
    ClusterCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        "clusterrolebindings",
        "cluster-agent-node-control",
    ),
    ClusterCleanupResource(
        "rbac.authorization.k8s.io",
        "v1",
        "clusterroles",
        "cluster-agent-node-control",
    ),
    ClusterCleanupResource(
        "rbac.authorization.k8s.io", "v1", "clusterrolebindings", "cluster-agent-read"
    ),
    ClusterCleanupResource("rbac.authorization.k8s.io", "v1", "clusterroles", "cluster-agent-read"),
)
FINAL_AGENT_DEPLOYMENT = NamespacedCleanupResource(
    "apps", "v1", TARGET_NAMESPACE, "deployments", "cluster-agent"
)
FINAL_AGENT_SERVICE_ACCOUNT = NamespacedCleanupResource(
    "core", "v1", TARGET_NAMESPACE, "serviceaccounts", "cluster-agent"
)
FINAL_UNINSTALL_CLUSTER_ROLE_BINDING = ClusterCleanupResource(
    "rbac.authorization.k8s.io",
    "v1",
    "clusterrolebindings",
    "cluster-agent-uninstall",
)
FINAL_UNINSTALL_CLUSTER_ROLE = ClusterCleanupResource(
    "rbac.authorization.k8s.io", "v1", "clusterroles", "cluster-agent-uninstall"
)
FINAL_CASCADE_NAMESPACED_CLEANUP = (
    FINAL_AGENT_DEPLOYMENT,
    FINAL_AGENT_SERVICE_ACCOUNT,
)
FINAL_CASCADE_CLUSTER_CLEANUP = (FINAL_UNINSTALL_CLUSTER_ROLE_BINDING,)


def namespaced_cleanup_ref(item: NamespacedCleanupResource) -> str:
    kind = item.resource.removesuffix("s")
    return f"{item.namespace}:{kind}/{item.name}"


def cluster_cleanup_ref(item: ClusterCleanupResource) -> str:
    kind = item.resource.removesuffix("s")
    return f"cluster:{kind}/{item.name}"


UNINSTALL_CLEANUP_RESOURCE_REFS = tuple(
    [*(namespaced_cleanup_ref(item) for item in PRE_ACK_NAMESPACED_CLEANUP)]
    + [*(cluster_cleanup_ref(item) for item in PRE_ACK_CLUSTER_CLEANUP)]
    + [*(namespaced_cleanup_ref(item) for item in FINAL_CASCADE_NAMESPACED_CLEANUP)]
    + [*(cluster_cleanup_ref(item) for item in FINAL_CASCADE_CLUSTER_CLEANUP)]
    + [cluster_cleanup_ref(FINAL_UNINSTALL_CLUSTER_ROLE)]
)
FINAL_CLEANUP_RESOURCE_REFS = tuple(
    [*(namespaced_cleanup_ref(item) for item in FINAL_CASCADE_NAMESPACED_CLEANUP)]
    + [*(cluster_cleanup_ref(item) for item in FINAL_CASCADE_CLUSTER_CLEANUP)]
    + [cluster_cleanup_ref(FINAL_UNINSTALL_CLUSTER_ROLE)]
)


@dataclass(frozen=True)
class QueuedAgentUninstall:
    command_id: str
    correlation_id: str
    inserted: bool


def agent_uninstall_plan(
    *, cluster_id: str, workspace_id: str, requested_by: str, correlation_id: str
) -> JsonObject:
    payload = {
        "cluster_id": cluster_id,
        "contract_version": UNINSTALL_CONTRACT_VERSION,
    }
    basis = {
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "action": Command.CLUSTER_AGENT_UNINSTALL_ACTION,
        "payload": payload,
        "correlation_id": correlation_id,
    }
    encoded = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return {
        "command_id": f"cmd-uninstall-{digest[:24]}",
        "idempotency_key": digest,
        "cluster_id": cluster_id,
        "action": Command.CLUSTER_AGENT_UNINSTALL_ACTION,
        "namespace": TARGET_NAMESPACE,
        "diff": {
            "resource": "opsia/cluster-agent",
            "namespace": TARGET_NAMESPACE,
            "risk": RiskLevel.REVIEW_REQUIRED.value,
            "basis": {"contract_version": UNINSTALL_CONTRACT_VERSION},
        },
        "payload": payload,
        "steps": ["acknowledge uninstall", "remove allowlisted agent runtime"],
        "lease": {
            "lease_seconds": DEFAULT_COMMAND_LEASE_SECONDS,
            "heartbeat_interval_seconds": DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
        },
        "retry_policy": {
            "max_attempts": DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
            "retry_delay_seconds": DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
        },
        "routing_constraint": {
            "channel": "agent",
            "cluster_id": cluster_id,
            "workspace_id": workspace_id,
            "required_capability": "commands",
        },
        "workspace_id": workspace_id,
        "priority": UNINSTALL_PRIORITY,
        "requested_by": requested_by,
        "reason": "cluster disconnect requested by administrator",
        "correlation_id": correlation_id,
    }


def queue_agent_uninstall(
    db: Any, *, cluster_id: str, workspace_id: str, requested_by: str
) -> QueuedAgentUninstall:
    correlation_id = f"corr-uninstall-{uuid.uuid4()}"
    plan = agent_uninstall_plan(
        cluster_id=cluster_id,
        workspace_id=workspace_id,
        requested_by=requested_by,
        correlation_id=correlation_id,
    )
    inserted = bool(db.queue_agent_command(correlation_id, plan, CommandStatus.QUEUED))
    return QueuedAgentUninstall(
        command_id=str(plan["command_id"]),
        correlation_id=correlation_id,
        inserted=inserted,
    )
