"""Declarative resource-action catalog used by capability discovery and execution.

The catalog is the only place that binds a resource shape to its permission,
agent capability, command transport, UI input fields, and gateway route.  Web
clients consume the resulting descriptor and never repeat an action list.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from domains.command.actions import command_action_spec
from packages.config.constants import Command
from packages.config.control import control_namespace_allowed
from packages.config.terminal import pod_exec_namespace_allowed
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import MAX_DEPLOYMENT_REPLICAS
from packages.contracts.gateway.responses import (
    ResourceActionCapability,
    ResourceCapabilityInput,
    ResourceCapabilitySubject,
)
from packages.contracts.identity import Permission
from packages.contracts.resource_files import RESOURCE_FILE_AGENT_CAPABILITY
from packages.contracts.terminal import POD_EXEC_AGENT_CAPABILITY

NamespacePolicy = Literal["control", "terminal", "cluster", "resource"]
ExecutionTransport = Literal["command", "terminal", "resource-files"]
RequestContext = Literal["simple", "exact-resource", "rollback"]
ResultIntent = Literal[
    "refresh-resource",
    "resource-summary",
    "terminal-session",
    "resource-files",
]
ResourceState = Literal[
    "always",
    "deletable",
    "cronjob-running",
    "cronjob-suspended",
    "node-cordoned",
    "node-schedulable",
    "rollback-available",
]


@dataclass(frozen=True)
class ResourceActionDefinition:
    capability_id: str
    label: str
    description: str
    execution: ExecutionTransport
    method: Literal["POST", "WEBSOCKET"]
    path_template: str
    resource_type: str | None
    kind: str | None
    permission: str
    agent_capability: str
    namespace_policy: NamespacePolicy
    command_action: str | None = None
    inputs: tuple[ResourceCapabilityInput, ...] = ()
    resource_state: ResourceState = "always"
    request_context: RequestContext = "simple"
    result_intent: ResultIntent = "refresh-resource"

    def applies_to(
        self,
        subject: ResourceCapabilitySubject,
        resource: Mapping[str, Any],
    ) -> bool:
        if (
            self.resource_type is not None
            and subject.resource_type.casefold() != self.resource_type
        ):
            return False
        if self.kind is not None and subject.kind.casefold() != self.kind:
            return False
        if self.namespace_policy == "cluster":
            allowed_namespace = subject.namespace is None
        elif self.namespace_policy == "resource":
            allowed_namespace = True
        elif subject.namespace is None:
            return False
        elif self.namespace_policy == "control":
            allowed_namespace = control_namespace_allowed(subject.namespace)
        elif self.namespace_policy == "terminal":
            allowed_namespace = pod_exec_namespace_allowed(subject.namespace)
        else:
            allowed_namespace = pod_exec_namespace_allowed(subject.namespace)
        if not allowed_namespace:
            return False
        if not _resource_state_matches(self.resource_state, resource):
            return False
        return self.command_action is None or _command_action_allows(
            self.command_action, subject.namespace
        )

    def render(self, subject: ResourceCapabilitySubject) -> ResourceActionCapability:
        values = {
            "cluster_id": quote(subject.cluster_id, safe=""),
            "namespace": quote(subject.namespace or "", safe=""),
            "deployment": quote(subject.name, safe=""),
            "cronjob": quote(subject.name, safe=""),
            "kind": quote(subject.kind.casefold(), safe=""),
            "workload": quote(subject.name, safe=""),
            "node": quote(subject.name, safe=""),
            "pod": quote(subject.name, safe=""),
            "resource_id": quote(subject.resource_id, safe=""),
        }
        return ResourceActionCapability(
            capability_id=self.capability_id,
            label=self.label,
            description=self.description,
            execution=self.execution,
            confirmation_required=True,
            realtime=True,
            input_schema=list(self.inputs),
            method=self.method,
            path=self.path_template.format(**values),
            request_context=self.request_context,
            result_intent=self.result_intent,
        )


def _command_action_allows(action: str, namespace: str | None) -> bool:
    spec = command_action_spec(action)
    return spec is not None and spec.allows_namespace(namespace or "")


RESOURCE_ACTIONS: tuple[ResourceActionDefinition, ...] = (
    ResourceActionDefinition(
        capability_id="workload.rollback",
        label="Rollback",
        description="Restore an exact observed Deployment revision and stream the result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.RESOURCE_WORKLOAD_ROLLBACK_PATH,
        resource_type="workload",
        kind="deployment",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_WORKLOAD_ROLLBACK_CAPABILITY,
        namespace_policy="control",
        command_action=Command.KUBERNETES_DEPLOYMENT_ROLLBACK_ACTION,
        resource_state="rollback-available",
        request_context="rollback",
    ),
    ResourceActionDefinition(
        capability_id="workload.rollback",
        label="Rollback",
        description="Restore an exact observed StatefulSet revision and stream the result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.RESOURCE_WORKLOAD_ROLLBACK_PATH,
        resource_type="workload",
        kind="statefulset",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_WORKLOAD_ROLLBACK_CAPABILITY,
        namespace_policy="control",
        command_action=Command.KUBERNETES_STATEFULSET_ROLLBACK_ACTION,
        resource_state="rollback-available",
        request_context="rollback",
    ),
    ResourceActionDefinition(
        capability_id="workload.rollback",
        label="Rollback",
        description="Restore an exact observed DaemonSet revision and stream the result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.RESOURCE_WORKLOAD_ROLLBACK_PATH,
        resource_type="workload",
        kind="daemonset",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_WORKLOAD_ROLLBACK_CAPABILITY,
        namespace_policy="control",
        command_action=Command.KUBERNETES_DAEMONSET_ROLLBACK_ACTION,
        resource_state="rollback-available",
        request_context="rollback",
    ),
    ResourceActionDefinition(
        capability_id="resource.delete",
        label="Delete",
        description="Delete this exact resource after reviewing its owner-reference cascade.",
        execution="command",
        method="POST",
        path_template=gateway_routes.RESOURCE_DELETE_PATH,
        resource_type=None,
        kind=None,
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_RESOURCE_DELETE_CAPABILITY,
        namespace_policy="resource",
        command_action=Command.KUBERNETES_RESOURCE_DELETE_ACTION,
        resource_state="deletable",
    ),
    ResourceActionDefinition(
        capability_id="deployment.restart",
        label="Restart",
        description="Restart this deployment and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_DEPLOYMENT_RESTART_PATH,
        resource_type="workload",
        kind="deployment",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability="command_receiver",
        namespace_policy="control",
        command_action=Command.DEFAULT_ACTION,
    ),
    ResourceActionDefinition(
        capability_id="deployment.scale",
        label="Scale",
        description="Change the desired replica count and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_DEPLOYMENT_SCALE_PATH,
        resource_type="workload",
        kind="deployment",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability="command_receiver",
        namespace_policy="control",
        command_action=Command.KUBERNETES_DEPLOYMENT_SCALE_ACTION,
        inputs=(
            ResourceCapabilityInput(
                key="replicas",
                label="Replicas",
                type="integer",
                required=True,
                minimum=0,
                maximum=MAX_DEPLOYMENT_REPLICAS,
                default=1,
            ),
        ),
    ),
    ResourceActionDefinition(
        capability_id="statefulset.restart",
        label="Restart",
        description="Restart this StatefulSet and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_WORKLOAD_RESTART_PATH,
        resource_type="workload",
        kind="statefulset",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability="command_receiver",
        namespace_policy="control",
        command_action=Command.KUBERNETES_STATEFULSET_RESTART_ACTION,
    ),
    ResourceActionDefinition(
        capability_id="statefulset.scale",
        label="Scale",
        description="Change the desired replica count and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_WORKLOAD_SCALE_PATH,
        resource_type="workload",
        kind="statefulset",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability="command_receiver",
        namespace_policy="control",
        command_action=Command.KUBERNETES_STATEFULSET_SCALE_ACTION,
        inputs=(
            ResourceCapabilityInput(
                key="replicas",
                label="Replicas",
                type="integer",
                required=True,
                minimum=0,
                maximum=MAX_DEPLOYMENT_REPLICAS,
                default=1,
            ),
        ),
    ),
    ResourceActionDefinition(
        capability_id="daemonset.restart",
        label="Restart",
        description="Restart this DaemonSet and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_WORKLOAD_RESTART_PATH,
        resource_type="workload",
        kind="daemonset",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability="command_receiver",
        namespace_policy="control",
        command_action=Command.KUBERNETES_DAEMONSET_RESTART_ACTION,
    ),
    ResourceActionDefinition(
        capability_id="node.cordon",
        label="Cordon",
        description="Mark this node unschedulable and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_NODE_CORDON_PATH,
        resource_type="node",
        kind="node",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_NODE_CONTROL_CAPABILITY,
        namespace_policy="cluster",
        command_action=Command.KUBERNETES_NODE_CORDON_ACTION,
        resource_state="node-schedulable",
    ),
    ResourceActionDefinition(
        capability_id="node.uncordon",
        label="Uncordon",
        description="Mark this node schedulable and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_NODE_UNCORDON_PATH,
        resource_type="node",
        kind="node",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_NODE_CONTROL_CAPABILITY,
        namespace_policy="cluster",
        command_action=Command.KUBERNETES_NODE_UNCORDON_ACTION,
        resource_state="node-cordoned",
    ),
    ResourceActionDefinition(
        capability_id="node.drain",
        label="Drain",
        description="Cordon this exact node and evict eligible Pods with bounded progress.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_NODE_DRAIN_PATH,
        resource_type="node",
        kind="node",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_NODE_CONTROL_CAPABILITY,
        namespace_policy="cluster",
        command_action=Command.KUBERNETES_NODE_DRAIN_ACTION,
        request_context="exact-resource",
        result_intent="resource-summary",
        inputs=(
            ResourceCapabilityInput(
                key="timeout_seconds",
                label="Timeout (seconds)",
                type="integer",
                required=True,
                minimum=10,
                maximum=600,
                default=60,
            ),
            ResourceCapabilityInput(
                key="max_parallel",
                label="Parallel evictions",
                type="integer",
                required=True,
                minimum=1,
                maximum=32,
                default=8,
            ),
            ResourceCapabilityInput(
                key="max_pods",
                label="Pod safety limit",
                type="integer",
                required=True,
                minimum=1,
                maximum=5000,
                default=1000,
            ),
            ResourceCapabilityInput(
                key="force",
                label="Evict unmanaged Pods",
                type="boolean",
                required=True,
                minimum=None,
                maximum=None,
                default=False,
            ),
            ResourceCapabilityInput(
                key="delete_empty_dir_data",
                label="Evict Pods using emptyDir",
                type="boolean",
                required=True,
                minimum=None,
                maximum=None,
                default=False,
            ),
        ),
    ),
    ResourceActionDefinition(
        capability_id="node.debug",
        label="Debug node",
        description="Create one owned, auditable debug Pod on this exact node.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_NODE_DEBUG_PATH,
        resource_type="node",
        kind="node",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_NODE_CONTROL_CAPABILITY,
        namespace_policy="cluster",
        command_action=Command.KUBERNETES_NODE_DEBUG_ACTION,
        request_context="exact-resource",
        result_intent="terminal-session",
        inputs=(
            ResourceCapabilityInput(
                key="namespace",
                label="Namespace",
                type="string",
                required=True,
                minimum=None,
                maximum=None,
                default="",
            ),
            ResourceCapabilityInput(
                key="image",
                label="Digest-pinned debug image",
                type="string",
                required=True,
                minimum=None,
                maximum=None,
                default="",
            ),
        ),
    ),
    ResourceActionDefinition(
        capability_id="node.debug.cleanup",
        label="Clean up debug Pod",
        description="Delete only the debug Pod owned by the supplied session.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_NODE_DEBUG_CLEANUP_PATH,
        resource_type="node",
        kind="node",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_NODE_CONTROL_CAPABILITY,
        namespace_policy="cluster",
        command_action=Command.KUBERNETES_NODE_DEBUG_CLEANUP_ACTION,
        request_context="exact-resource",
        inputs=(
            ResourceCapabilityInput(
                key="namespace",
                label="Namespace",
                type="string",
                required=True,
                minimum=None,
                maximum=None,
                default="",
                prefill_result_key="namespace",
            ),
            ResourceCapabilityInput(
                key="session_id",
                label="Debug session ID",
                type="string",
                required=True,
                minimum=None,
                maximum=None,
                default="",
                prefill_result_key="session_id",
            ),
        ),
    ),
    ResourceActionDefinition(
        capability_id="pod.debug",
        label="Debug container",
        description="Attach an ephemeral debug container to this exact Pod and target container.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_POD_DEBUG_PATH,
        resource_type="pod",
        kind="pod",
        permission=Permission.POD_EXEC.value,
        agent_capability=Command.KUBERNETES_DEBUG_CAPABILITY,
        namespace_policy="control",
        command_action=Command.KUBERNETES_POD_DEBUG_ACTION,
        request_context="exact-resource",
        result_intent="terminal-session",
        inputs=(
            ResourceCapabilityInput(
                key="target_container",
                label="Target container",
                type="string",
                required=True,
                minimum=None,
                maximum=None,
                default="",
            ),
            ResourceCapabilityInput(
                key="image",
                label="Digest-pinned debug image",
                type="string",
                required=True,
                minimum=None,
                maximum=None,
                default="",
            ),
        ),
    ),
    ResourceActionDefinition(
        capability_id="pod.exec",
        label="Terminal",
        description="Open an audited terminal session and stream its output.",
        execution="terminal",
        method="WEBSOCKET",
        path_template="/live/terminal",
        resource_type="pod",
        kind="pod",
        permission=Permission.POD_EXEC.value,
        agent_capability=POD_EXEC_AGENT_CAPABILITY,
        namespace_policy="terminal",
    ),
    ResourceActionDefinition(
        capability_id="image.filesystem",
        label="Image files",
        description="Inspect the selected Pod container image through the outbound Agent.",
        execution="resource-files",
        method="POST",
        path_template=gateway_routes.RESOURCE_FILE_COMMAND_PATH,
        resource_type="pod",
        kind="pod",
        permission=Permission.INVENTORY_READ.value,
        agent_capability=RESOURCE_FILE_AGENT_CAPABILITY,
        namespace_policy="resource",
        request_context="exact-resource",
        result_intent="resource-files",
    ),
    ResourceActionDefinition(
        capability_id="pod.filesystem",
        label="Pod files",
        description="Browse the selected live Pod container through the outbound Agent.",
        execution="resource-files",
        method="POST",
        path_template=gateway_routes.RESOURCE_FILE_COMMAND_PATH,
        resource_type="pod",
        kind="pod",
        permission=Permission.INVENTORY_READ.value,
        agent_capability=RESOURCE_FILE_AGENT_CAPABILITY,
        namespace_policy="resource",
        request_context="exact-resource",
        result_intent="resource-files",
    ),
    ResourceActionDefinition(
        capability_id="cronjob.resume",
        label="Resume",
        description="Resume this CronJob and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_CRONJOB_RESUME_PATH,
        resource_type="workload",
        kind="cronjob",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_CRONJOB_CONTROL_CAPABILITY,
        namespace_policy="control",
        command_action=Command.KUBERNETES_CRONJOB_RESUME_ACTION,
        resource_state="cronjob-suspended",
        request_context="exact-resource",
    ),
    ResourceActionDefinition(
        capability_id="cronjob.suspend",
        label="Suspend",
        description="Suspend this CronJob and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_CRONJOB_SUSPEND_PATH,
        resource_type="workload",
        kind="cronjob",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_CRONJOB_CONTROL_CAPABILITY,
        namespace_policy="control",
        command_action=Command.KUBERNETES_CRONJOB_SUSPEND_ACTION,
        resource_state="cronjob-running",
        request_context="exact-resource",
    ),
    ResourceActionDefinition(
        capability_id="cronjob.trigger",
        label="Trigger",
        description="Create one Job from this CronJob and stream the operation result.",
        execution="command",
        method="POST",
        path_template=gateway_routes.CLUSTER_CRONJOB_TRIGGER_PATH,
        resource_type="workload",
        kind="cronjob",
        permission=Permission.DEPLOY_RUN.value,
        agent_capability=Command.KUBERNETES_CRONJOB_CONTROL_CAPABILITY,
        namespace_policy="control",
        command_action=Command.KUBERNETES_CRONJOB_TRIGGER_ACTION,
        request_context="exact-resource",
    ),
)


def applicable_resource_actions(
    subject: ResourceCapabilitySubject,
    resource: Mapping[str, Any],
) -> tuple[ResourceActionDefinition, ...]:
    """Return only catalog definitions whose immutable resource policy applies."""
    return tuple(
        definition for definition in RESOURCE_ACTIONS if definition.applies_to(subject, resource)
    )


def resource_action_capability_id(command_action: str) -> str | None:
    """Resolve a command action through the canonical resource-action catalog."""

    return next(
        (
            definition.capability_id
            for definition in RESOURCE_ACTIONS
            if definition.command_action == command_action
        ),
        None,
    )


def _resource_state_matches(state: ResourceState, resource: Mapping[str, Any]) -> bool:
    if state == "always":
        return True
    if state == "deletable":
        return bool(
            str(resource.get("uid") or "").strip()
            and str(resource.get("resource_version") or "").strip()
            and resource.get("deleted_at") is None
        )
    if state == "rollback-available":
        raw = resource.get("raw")
        raw_object = raw if isinstance(raw, Mapping) else {}
        return bool(
            str(resource.get("uid") or "").strip()
            and str(resource.get("resource_version") or "").strip()
            and isinstance(raw_object.get("pod_template"), Mapping)
            and raw_object.get("revision_history_complete") is True
            and isinstance(raw_object.get("revision_history_count"), int)
            and int(raw_object["revision_history_count"]) > 1
        )
    raw = resource.get("raw")
    raw_object = raw if isinstance(raw, Mapping) else {}
    spec = raw_object.get("spec")
    spec_object = spec if isinstance(spec, Mapping) else {}
    if state in {"node-cordoned", "node-schedulable"}:
        unschedulable = spec_object.get("unschedulable")
        cordoned = unschedulable if isinstance(unschedulable, bool) else False
        return cordoned if state == "node-cordoned" else not cordoned
    suspended = spec_object.get("suspend")
    if not isinstance(suspended, bool):
        return False
    return suspended if state == "cronjob-suspended" else not suspended
