from domains.target.evidence_policy import default_agent_policy
from domains.target.policy_upgrade import build_target_upgrade_plan
from packages.contracts.evidence_policy import (
    TEMPO_RECENT_TRACE_QUERY_NAME,
    TEMPO_RECENT_TRACE_RANGE_SECONDS,
)
from packages.contracts.gateway.requests import AgentPolicy
from packages.contracts.target import (
    NODE_COLLECTOR_IMAGE_KEY,
    TARGET_AGENT_IMAGE_KEY,
    TARGET_NAMESPACE,
    TARGET_OTEL_TRACES_ENDPOINT,
    TARGET_RUNTIME_CONFIG_NAME,
)

OLD_IMAGE = f"registry.example.test/opsia@sha256:{'a' * 64}"
NEW_IMAGE = f"registry.example.test/opsia@sha256:{'b' * 64}"


def legacy_policy(cluster_id: str) -> AgentPolicy:
    body = default_agent_policy(cluster_id=cluster_id).model_dump()
    body["evidence"]["providers"]["traces"]["enabled"] = False
    return AgentPolicy.model_validate(body)


def desired_runtime_config(policy: AgentPolicy) -> dict[str, object]:
    for resource in policy.bootstrap.resources:
        if (
            resource.kind == "ConfigMap"
            and resource.namespace == TARGET_NAMESPACE
            and resource.name == TARGET_RUNTIME_CONFIG_NAME
        ):
            return resource.state
    raise AssertionError("target runtime ConfigMap was not planned")


def desired_agent_deployment(policy: AgentPolicy) -> dict[str, object]:
    for resource in (*policy.bootstrap.resources, *policy.desired_state.resources):
        if (
            resource.kind == "Deployment"
            and resource.namespace == TARGET_NAMESPACE
            and resource.name == "cluster-agent"
        ):
            return resource.state
    raise AssertionError("target Agent Deployment was not planned")


def test_policy_upgrade_enables_traces_and_keeps_self_upgrade_image_only() -> None:
    cluster_id = "legacy-target"
    plan = build_target_upgrade_plan(
        registration={
            "id": 1,
            "workspace_id": "default",
            "cluster_id": cluster_id,
            "settings": {
                "name": "legacy-target",
                "cluster_role": "target",
                "image": OLD_IMAGE,
                "otel_traces_endpoint": "",
            },
        },
        policy=legacy_policy(cluster_id),
        desired_states=[],
        target_image=NEW_IMAGE,
        rbac_actual_version=None,
    )

    assert plan.changed is True
    assert plan.policy is not None
    assert plan.policy.evidence.providers["traces"].enabled is True
    assert plan.policy.evidence.providers["traces"].queries
    assert plan.settings_patch["otel_traces_endpoint"] == TARGET_OTEL_TRACES_ENDPOINT
    runtime_config = desired_runtime_config(plan.policy)
    assert runtime_config["data"] == {
        TARGET_AGENT_IMAGE_KEY: NEW_IMAGE,
        NODE_COLLECTOR_IMAGE_KEY: NEW_IMAGE,
    }


def test_policy_upgrade_keeps_tempo_query_executable_by_previous_agent() -> None:
    """A previous Agent must accept the policy that upgrades its own Deployment."""

    cluster_id = "legacy-target"
    plan = build_target_upgrade_plan(
        registration={
            "id": 1,
            "workspace_id": "default",
            "cluster_id": cluster_id,
            "settings": {
                "name": "legacy-target",
                "cluster_role": "target",
                "image": OLD_IMAGE,
            },
        },
        policy=legacy_policy(cluster_id),
        desired_states=[],
        target_image=NEW_IMAGE,
        rbac_actual_version=None,
    )

    assert plan.policy is not None
    trace_query = next(
        query
        for query in plan.policy.evidence.providers["traces"].queries
        if query.get("name") == TEMPO_RECENT_TRACE_QUERY_NAME
    )
    assert "range_seconds" not in trace_query

    deployment = desired_agent_deployment(plan.policy)
    containers = deployment["spec"]["template"]["spec"]["containers"]
    agent = next(container for container in containers if container["name"] == "cluster-agent")
    assert agent["image"] == NEW_IMAGE


def test_policy_upgrade_retries_a_persisted_incompatible_generation() -> None:
    """A failed generation is rebased even when registration already names the new image."""

    cluster_id = "legacy-target"
    staged = build_target_upgrade_plan(
        registration={
            "id": 1,
            "workspace_id": "default",
            "cluster_id": cluster_id,
            "settings": {
                "name": "legacy-target",
                "cluster_role": "target",
                "image": OLD_IMAGE,
            },
        },
        policy=legacy_policy(cluster_id),
        desired_states=[],
        target_image=NEW_IMAGE,
        rbac_actual_version=None,
    )
    assert staged.policy is not None
    failed_body = staged.policy.model_dump()
    failed_body["generation"] = 13
    failed_trace_query = next(
        query
        for query in failed_body["evidence"]["providers"]["traces"]["queries"]
        if query.get("name") == TEMPO_RECENT_TRACE_QUERY_NAME
    )
    failed_trace_query["range_seconds"] = TEMPO_RECENT_TRACE_RANGE_SECONDS
    failed_policy = AgentPolicy.model_validate(failed_body)

    retry = build_target_upgrade_plan(
        registration={
            "id": 1,
            "workspace_id": "default",
            "cluster_id": cluster_id,
            "settings": {
                "name": "legacy-target",
                "cluster_role": "target",
                "image": NEW_IMAGE,
            },
        },
        policy=failed_policy,
        desired_states=[],
        target_image=NEW_IMAGE,
        rbac_actual_version=None,
    )

    assert retry.changed is True
    assert retry.current_generation == 13
    assert retry.next_generation == 14
    assert retry.policy is not None
    retried_trace_query = next(
        query
        for query in retry.policy.evidence.providers["traces"].queries
        if query.get("name") == TEMPO_RECENT_TRACE_QUERY_NAME
    )
    assert "range_seconds" not in retried_trace_query


def test_policy_upgrade_skips_display_only_dashboard_cluster() -> None:
    cluster_id = "apn2-match-prod"
    plan = build_target_upgrade_plan(
        registration={
            "id": 9,
            "workspace_id": "default",
            "cluster_id": cluster_id,
            "settings": {
                "name": "apn2-match-prod",
                "cluster_role": "display_only",
                "image": OLD_IMAGE,
            },
        },
        policy=default_agent_policy(cluster_id=cluster_id),
        desired_states=[],
        target_image=NEW_IMAGE,
        rbac_actual_version=None,
    )

    assert plan.changed is False
    assert plan.policy is None
    assert plan.skipped_reason == "unsupported_cluster_role"
