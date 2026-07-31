"""management 클러스터 읽기 전용 보호 규칙."""

from __future__ import annotations

from typing import Any

from packages.contracts.gateway.requests import (
    AgentPolicy,
    BootstrapPolicy,
    DesiredResource,
    DesiredStatePolicy,
    SchedulingPolicy,
)
from packages.contracts.target import KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS

TARGET_CLUSTER_ROLE = "target"
MANAGEMENT_CLUSTER_ROLE = "management"
MANAGEMENT_BOOTSTRAP_MODE = "management"
MANAGEMENT_READONLY_CODE = "management_readonly"
MANAGEMENT_READONLY_DETAIL = "management 클러스터는 읽기 전용입니다"


def management_readonly_detail() -> dict[str, str]:
    return {"code": MANAGEMENT_READONLY_CODE, "detail": MANAGEMENT_READONLY_DETAIL}


def cluster_role_from_settings(settings: object) -> str:
    if isinstance(settings, dict):
        role = settings.get("cluster_role")
        if isinstance(role, str) and role:
            return role
    return TARGET_CLUSTER_ROLE


def cluster_role_from_registration(registration: dict[str, Any] | None) -> str:
    if not registration:
        return TARGET_CLUSTER_ROLE
    return cluster_role_from_settings(registration.get("settings"))


def cluster_role_from_policy(policy: dict[str, Any] | AgentPolicy | None) -> str:
    if policy is None:
        return TARGET_CLUSTER_ROLE
    if isinstance(policy, AgentPolicy):
        return policy.cluster_role
    role = policy.get("cluster_role") if isinstance(policy, dict) else None
    return str(role) if role else TARGET_CLUSTER_ROLE


def is_management_role(role: str) -> bool:
    return role == MANAGEMENT_CLUSTER_ROLE


def is_management_registration(registration: dict[str, Any] | None) -> bool:
    return is_management_role(cluster_role_from_registration(registration))


def freeze_management_policy(policy: AgentPolicy) -> AgentPolicy:
    """management 정책에서 제어/재조정/스케줄링 자원은 항상 비운다."""
    providers = dict(policy.evidence.providers)
    kubernetes = providers.get("kubernetes")
    if kubernetes is not None:
        providers["kubernetes"] = kubernetes.model_copy(
            update={
                "queries": [
                    query
                    for query in kubernetes.queries
                    if query.get("collection_scope")
                    != KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS
                ]
            }
        )
    return policy.model_copy(
        update={
            "cluster_role": MANAGEMENT_CLUSTER_ROLE,
            "evidence": policy.evidence.model_copy(
                update={"profile": "management", "providers": providers}
            ),
            "bootstrap": BootstrapPolicy(mode=MANAGEMENT_BOOTSTRAP_MODE, resources=[]),
            "desired_state": DesiredStatePolicy(resources=[]),
            "scheduling": SchedulingPolicy(),
        },
    )


def refresh_management_policy(policy: AgentPolicy) -> AgentPolicy:
    """Return a new generation only when a stored management policy violates invariants."""

    frozen = freeze_management_policy(policy)
    current = policy.model_dump(mode="json")
    normalized = frozen.model_dump(mode="json")
    current.pop("generation", None)
    normalized.pop("generation", None)
    if current == normalized:
        return policy
    return frozen.model_copy(update={"generation": policy.generation + 1})


def desired_resources(policy: AgentPolicy) -> list[DesiredResource]:
    return [*policy.bootstrap.resources, *policy.desired_state.resources]


def has_write_or_command_policy(policy: AgentPolicy) -> bool:
    return bool(desired_resources(policy))


def has_scheduling_policy(policy: AgentPolicy) -> bool:
    return bool(policy.scheduling.profiles)


def management_policy_update_is_forbidden(payload: AgentPolicy) -> bool:
    role_changed = (
        "cluster_role" in payload.model_fields_set
        and payload.cluster_role != MANAGEMENT_CLUSTER_ROLE
    )
    return role_changed or has_write_or_command_policy(payload) or has_scheduling_policy(payload)
