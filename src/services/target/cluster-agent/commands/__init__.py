from __future__ import annotations

from commands.context import CommandContext, CommandResult
from commands.gitops import GitOpsResourceCommandPayload, execute_gitops_resource_command
from commands.kubernetes import (
    KubernetesApiClient,
    KubernetesCronJobPayload,
    KubernetesGetPayload,
    KubernetesNodeDebugCleanupPayload,
    KubernetesNodeDebugPayload,
    KubernetesNodeDrainPayload,
    KubernetesNodeSchedulingPayload,
    KubernetesPatchPayload,
    KubernetesPodDebugPayload,
    KubernetesScalePayload,
    KubernetesWorkloadRollbackPayload,
    cronjob_job_body,
    kubernetes_generate_name,
    rollback_template_from_revision,
    validate_cronjob_resource_ref,
    validate_exact_resource,
    workload_template_sha256,
)
from commands.outbox import CommandResultOutbox, CommandResultRecord
from commands.registry import (
    AgentCommandRegistry,
    command,
    command_handler,
    kubernetes_command,
)

__all__ = [
    "AgentCommandRegistry",
    "CommandContext",
    "CommandResult",
    "CommandResultOutbox",
    "CommandResultRecord",
    "KubernetesApiClient",
    "KubernetesCronJobPayload",
    "KubernetesGetPayload",
    "KubernetesNodeSchedulingPayload",
    "KubernetesNodeDebugCleanupPayload",
    "KubernetesNodeDebugPayload",
    "KubernetesNodeDrainPayload",
    "KubernetesPodDebugPayload",
    "KubernetesPatchPayload",
    "KubernetesScalePayload",
    "KubernetesWorkloadRollbackPayload",
    "GitOpsResourceCommandPayload",
    "cronjob_job_body",
    "kubernetes_generate_name",
    "validate_cronjob_resource_ref",
    "rollback_template_from_revision",
    "validate_exact_resource",
    "workload_template_sha256",
    "execute_gitops_resource_command",
    "command",
    "command_handler",
    "kubernetes_command",
]
