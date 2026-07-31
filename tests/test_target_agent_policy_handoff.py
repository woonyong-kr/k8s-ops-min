from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "src" / "services" / "target" / "cluster-agent"
sys.path.insert(0, str(AGENT_ROOT))

from control.policy import AgentPolicySync  # noqa: E402
from control.reconciler import DesiredStateReconciler  # noqa: E402
from control.store import AgentControlStore  # noqa: E402

from packages.contracts.gateway.requests import (  # noqa: E402
    AgentPolicy,
    BootstrapPolicy,
    DesiredResource,
    DesiredStatePolicy,
)

OLD_IMAGE = f"registry.example.test/opsia@sha256:{'a' * 64}"
NEW_IMAGE = f"registry.example.test/opsia@sha256:{'b' * 64}"


def target_agent_deployment(image: str) -> DesiredResource:
    return DesiredResource(
        resource_id="target-agent-deployment",
        scope="target-agent",
        kind="Deployment",
        namespace="target",
        name="cluster-agent",
        action="apply",
        state={
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "cluster-agent", "namespace": "target"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "cluster-agent", "image": image}],
                    }
                }
            },
        },
    )


def target_runtime_config(image: str) -> DesiredResource:
    return DesiredResource(
        resource_id="target-runtime-config-images",
        scope="target-agent",
        kind="ConfigMap",
        namespace="target",
        name="target-runtime-config",
        action="apply",
        state={
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "target-runtime-config", "namespace": "target"},
            "data": {
                "TARGET_AGENT_IMAGE": image,
                "NODE_COLLECTOR_IMAGE": image,
            },
        },
    )


def unrelated_target_config() -> DesiredResource:
    return DesiredResource(
        resource_id="unrelated-target-config",
        scope="target-agent",
        kind="ConfigMap",
        namespace="target",
        name="target-agent-policy",
        action="apply",
        state={
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "target-agent-policy", "namespace": "target"},
            "data": {"unrelated": "must-not-cross-failed-runtime-policy"},
        },
    )


def policy(
    generation: int,
    image: str,
    *,
    include_unrelated: bool = False,
) -> AgentPolicy:
    desired_resources = [target_agent_deployment(image)]
    if include_unrelated:
        desired_resources.append(unrelated_target_config())
    return AgentPolicy(
        cluster_id="target-1",
        generation=generation,
        bootstrap=BootstrapPolicy(resources=[target_runtime_config(image)]),
        desired_state=DesiredStatePolicy(resources=desired_resources),
    )


class PolicyClient:
    def __init__(self, incoming: AgentPolicy) -> None:
        self.incoming = incoming
        self.statuses: list[dict[str, Any]] = []

    async def fetch_policy(self, _cluster_id: str, _generation: int) -> dict[str, Any]:
        return self.incoming.model_dump()

    async def report_policy_status(self, status: dict[str, Any]) -> None:
        self.statuses.append(status)


class RecordingResourceApplier:
    def __init__(self) -> None:
        self.applied: list[DesiredResource] = []

    async def observe(self, _resource: DesiredResource) -> None:
        return None

    async def apply(self, resource: DesiredResource) -> None:
        self.applied.append(resource)


def test_failed_evidence_activation_stages_only_upgrade_reconcile_policy(
    tmp_path: Path,
) -> None:
    active = policy(12, OLD_IMAGE)
    incoming = policy(13, NEW_IMAGE, include_unrelated=True)
    client = PolicyClient(incoming)

    with AgentControlStore(str(tmp_path / "agent-control.db")) as store:
        store.save_policy(active)
        sync = AgentPolicySync(
            cluster_id="target-1",
            store=store,
            default_policy=active,
            apply_policy=lambda _policy: (_ for _ in ()).throw(
                ValueError("telemetry source does not support range query: tempo")
            ),
            interval_seconds=15,
        )

        result = asyncio.run(sync.sync_once(client))

        assert result == "failed"
        assert store.active_generation() == 12
        assert store.load_policy() == active
        reconcile_policy = store.load_reconcile_policy()
        assert reconcile_policy is not None
        assert reconcile_policy.generation == incoming.generation
        assert reconcile_policy.bootstrap.resources == [target_runtime_config(NEW_IMAGE)]
        assert reconcile_policy.desired_state.resources == [target_agent_deployment(NEW_IMAGE)]
        assert client.statuses[-1]["status"] == "failed"
        assert client.statuses[-1]["message"] == (
            "telemetry source does not support range query: tempo"
        )
        assert client.statuses[-1]["details"]["desired_state_handoff"] == {
            "status": "staged",
            "generation": 13,
            "resource_id": "target-agent-deployment",
        }

        applier = RecordingResourceApplier()
        reconciler = DesiredStateReconciler(
            cluster_id="target-1",
            cluster_role="target",
            store=store,
            interval_seconds=15,
            resource_applier=applier,
        )

        reconcile = asyncio.run(reconciler.reconcile_once())

        assert reconcile["generation"] == 13
        assert [resource.state for resource in applier.applied] == [
            target_runtime_config(NEW_IMAGE).state,
            target_agent_deployment(NEW_IMAGE).state,
        ]
        assert store.load_policy() == active


def test_failed_activation_without_agent_deployment_change_keeps_active_reconcile_policy(
    tmp_path: Path,
) -> None:
    active = policy(12, OLD_IMAGE)
    incoming = policy(13, OLD_IMAGE)
    client = PolicyClient(incoming)

    with AgentControlStore(str(tmp_path / "agent-control.db")) as store:
        store.save_policy(active)
        sync = AgentPolicySync(
            cluster_id="target-1",
            store=store,
            default_policy=active,
            apply_policy=lambda _policy: (_ for _ in ()).throw(ValueError("invalid query")),
            interval_seconds=15,
        )

        assert asyncio.run(sync.sync_once(client)) == "failed"

        assert store.load_pending_reconcile_policy() is None
        assert store.load_reconcile_policy() == active
        assert "desired_state_handoff" not in client.statuses[-1]["details"]


def test_failed_cross_cluster_policy_never_stages_an_upgrade_handoff(
    tmp_path: Path,
) -> None:
    active = policy(12, OLD_IMAGE)
    incoming = policy(13, NEW_IMAGE).model_copy(update={"cluster_id": "other-target"})
    client = PolicyClient(incoming)

    with AgentControlStore(str(tmp_path / "agent-control.db")) as store:
        store.save_policy(active)
        sync = AgentPolicySync(
            cluster_id="target-1",
            store=store,
            default_policy=active,
            apply_policy=lambda candidate: (_ for _ in ()).throw(
                ValueError(f"policy cluster_id does not match agent: {candidate.cluster_id}")
            ),
            interval_seconds=15,
        )

        assert asyncio.run(sync.sync_once(client)) == "failed"

        assert store.load_pending_reconcile_policy() is None
        assert store.load_reconcile_policy() == active
        assert "desired_state_handoff" not in client.statuses[-1]["details"]


def test_successful_policy_save_promotes_active_and_clears_staged_handoff(
    tmp_path: Path,
) -> None:
    active = policy(12, OLD_IMAGE)
    incoming = policy(13, NEW_IMAGE)

    with AgentControlStore(str(tmp_path / "agent-control.db")) as store:
        store.save_policy(active)
        store.save_pending_reconcile_policy(incoming)
        assert store.load_reconcile_policy() == incoming

        store.save_policy(incoming)

        assert store.active_generation() == 13
        assert store.load_policy() == incoming
        assert store.load_pending_reconcile_policy() is None
        assert store.load_reconcile_policy() == incoming
