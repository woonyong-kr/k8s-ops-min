from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from span import get_tracer

from control.store import AgentControlStore, desired_resource_hash
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.policy_merge import merge_agent_policy
from packages.contracts.gateway.requests import (
    AgentPolicy,
    BootstrapPolicy,
    DesiredResource,
    DesiredStatePolicy,
)
from packages.contracts.interfaces import ManagementPlaneClient
from packages.contracts.target import TARGET_RUNTIME_CONFIG_NAME, TargetComponent

TRACER = get_tracer("target-cluster-agent.policy")
LOGGER = get_logger(__name__)

PolicyApplier = Callable[[AgentPolicy], JsonObject]
PolicyStatusDetails = Callable[[], Awaitable[JsonObject]]
RuntimeConfigurationApplier = Callable[[ManagementPlaneClient, AgentPolicy], Awaitable[JsonObject]]


class AgentPolicySync:
    def __init__(
        self,
        *,
        cluster_id: str,
        store: AgentControlStore,
        default_policy: AgentPolicy,
        apply_policy: PolicyApplier,
        interval_seconds: int,
        status_details: PolicyStatusDetails | None = None,
        apply_runtime_configuration: RuntimeConfigurationApplier | None = None,
    ) -> None:
        self.cluster_id = cluster_id
        self.store = store
        self.default_policy = default_policy
        self.apply_policy = apply_policy
        self.interval_seconds = interval_seconds
        self.status_details = status_details
        self.apply_runtime_configuration = apply_runtime_configuration

    async def apply_runtime(self, client: ManagementPlaneClient, policy: AgentPolicy) -> JsonObject:
        if self.apply_runtime_configuration is None:
            return {}
        return dict(await self.apply_runtime_configuration(client, policy))

    async def runtime_status_details(self) -> JsonObject:
        if self.status_details is None:
            return {}
        try:
            return dict(await self.status_details())
        except Exception as exc:
            return {"status_details_error": type(exc).__name__}

    def apply_stored_or_default(self) -> JsonObject:
        stored_policy = self.store.load_policy()
        policy = (
            merge_agent_policy(self.default_policy, stored_policy)
            if stored_policy is not None
            else self.default_policy
        )
        details = self.apply_policy(policy)
        self.store.save_policy(policy)
        return details

    async def run(self, client: ManagementPlaneClient) -> None:
        while True:
            try:
                await self.sync_once(client)
            except Exception as exc:
                LOGGER.warning(
                    "policy_sync_failed",
                    extra={CONTEXT_KEY: {"cluster_id": self.cluster_id}},
                    exc_info=exc,
                )
            await asyncio.sleep(self.interval_seconds)

    async def sync_once(self, client: ManagementPlaneClient) -> str:
        with TRACER.start_as_current_span("policy.sync") as span:
            generation = self.store.active_generation()
            span.attr("policy.generation", generation)
            payload = await client.fetch_policy(self.cluster_id, generation)
            if payload is None:
                policy = self.store.load_policy() or self.default_policy
                try:
                    details = await self.apply_runtime(client, policy)
                    details.update(await self.runtime_status_details())
                    await client.report_policy_status(
                        {
                            "cluster_id": self.cluster_id,
                            "generation": generation,
                            "status": "unchanged",
                            "message": "policy unchanged",
                            "details": details,
                        }
                    )
                    return "unchanged"
                except Exception as exc:
                    span.error(exc)
                    await client.report_policy_status(
                        {
                            "cluster_id": self.cluster_id,
                            "generation": generation,
                            "status": "failed",
                            "message": str(exc),
                            "details": await self.runtime_status_details(),
                        }
                    )
                    return "failed"

            attempted_generation = self.payload_generation(payload, generation)
            active_policy = self.store.load_policy() or self.default_policy
            policy: AgentPolicy | None = None
            try:
                incoming_policy = AgentPolicy.model_validate(payload)
                policy = merge_agent_policy(
                    active_policy,
                    incoming_policy,
                )
                details = await self.apply_runtime(client, policy)
                details.update(self.apply_policy(policy))
                details.update(await self.runtime_status_details())
                self.store.save_policy(policy)
                await client.report_policy_status(
                    {
                        "cluster_id": self.cluster_id,
                        "generation": policy.generation,
                        "status": "applied",
                        "message": "policy applied",
                        "details": details,
                    }
                )
                return "applied"
            except Exception as exc:
                span.error(exc)
                details = await self.runtime_status_details()
                handoff = self.stage_desired_state_handoff(active_policy, policy)
                if handoff is not None:
                    details["desired_state_handoff"] = handoff
                await client.report_policy_status(
                    {
                        "cluster_id": self.cluster_id,
                        "generation": attempted_generation,
                        "status": "failed",
                        "message": str(exc),
                        "details": details,
                    }
                )
                return "failed"

    def stage_desired_state_handoff(
        self,
        active_policy: AgentPolicy,
        candidate_policy: AgentPolicy | None,
    ) -> JsonObject | None:
        if (
            candidate_policy is None
            or self.store.active_generation() >= candidate_policy.generation
            or candidate_policy.cluster_id != self.cluster_id
            or candidate_policy.cluster_role != active_policy.cluster_role
        ):
            return None
        active_deployment = self.target_agent_deployment(active_policy)
        candidate_deployment = self.target_agent_deployment(candidate_policy)
        if candidate_deployment is None:
            return None
        if active_deployment is not None and desired_resource_hash(
            active_deployment
        ) == desired_resource_hash(candidate_deployment):
            return None
        handoff_policy = self.self_upgrade_handoff_policy(
            active_policy,
            candidate_policy,
            candidate_deployment,
        )
        if not self.store.save_pending_reconcile_policy(handoff_policy):
            return None
        return {
            "status": "staged",
            "generation": candidate_policy.generation,
            "resource_id": candidate_deployment.resource_id,
        }

    @staticmethod
    def self_upgrade_handoff_policy(
        active_policy: AgentPolicy,
        candidate_policy: AgentPolicy,
        candidate_deployment: DesiredResource,
    ) -> AgentPolicy:
        runtime_configs = [
            resource
            for resource in candidate_policy.bootstrap.resources
            if (
                resource.scope == "target-agent"
                and resource.kind == "ConfigMap"
                and resource.name == TARGET_RUNTIME_CONFIG_NAME
                and resource.action == "apply"
            )
        ]
        return active_policy.model_copy(
            update={
                "generation": candidate_policy.generation,
                "bootstrap": BootstrapPolicy(
                    mode=candidate_policy.bootstrap.mode,
                    resources=runtime_configs,
                ),
                "desired_state": DesiredStatePolicy(resources=[candidate_deployment]),
            }
        )

    @staticmethod
    def target_agent_deployment(policy: AgentPolicy) -> DesiredResource | None:
        for resource in (*policy.bootstrap.resources, *policy.desired_state.resources):
            if (
                resource.scope == "target-agent"
                and resource.kind == "Deployment"
                and resource.name == TargetComponent.CLUSTER_AGENT
                and resource.action == "apply"
            ):
                return resource
        return None

    def payload_generation(self, payload: JsonObject, fallback: int) -> int:
        raw_generation = payload.get("generation")
        return raw_generation if isinstance(raw_generation, int) else fallback
