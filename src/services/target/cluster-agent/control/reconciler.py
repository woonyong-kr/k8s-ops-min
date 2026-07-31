from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import httpx
from span import get_tracer

from config import (
    DEFAULT_KUBERNETES_SERVICE_HOST,
    DEFAULT_KUBERNETES_SERVICE_PORT,
    DEFAULT_RECONCILER_MODE,
    KUBERNETES_API_TIMEOUT_SECONDS,
    KUBERNETES_SERVICE_HOST_ENV,
    KUBERNETES_SERVICE_PORT_ENV,
    KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
    KUBERNETES_SERVICEACCOUNT_TOKEN_PATH,
    RECONCILER_MODE_ARGOCD,
    RECONCILER_MODES,
)
from control.argocd_observer import ArgoObserver, unavailable_collection
from control.store import (
    AgentControlStore,
    ReconcileResult,
    desired_resource_hash,
)
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.settings import env
from packages.contracts.gateway.requests import AgentPolicy, DesiredResource
from packages.contracts.interfaces import ManagementPlaneClient
from packages.contracts.target import (
    NODE_COLLECTOR_IMAGE_KEY,
    TARGET_AGENT_IMAGE_KEY,
    TARGET_RUNTIME_CONFIG_NAME,
    require_target_image_digest,
)

TRACER = get_tracer("target-cluster-agent.reconciler")
LOGGER = get_logger(__name__)

RECONCILE_APPLIED = "applied"
RECONCILE_FAILED = "failed"
RECONCILE_UNCHANGED = "unchanged"


class ResourceApplier(Protocol):
    async def observe(self, resource: DesiredResource) -> None: ...

    async def apply(self, resource: DesiredResource) -> None: ...


class KubernetesResourceClient:
    def base_url(self) -> str:
        host = env(KUBERNETES_SERVICE_HOST_ENV, DEFAULT_KUBERNETES_SERVICE_HOST)
        port = env(KUBERNETES_SERVICE_PORT_ENV, DEFAULT_KUBERNETES_SERVICE_PORT)
        return f"https://{host}:{port}"

    def auth_headers(self) -> dict[str, str]:
        token = Path(KUBERNETES_SERVICEACCOUNT_TOKEN_PATH).read_text(encoding="utf-8").strip()
        return {"Authorization": f"Bearer {token}"}

    async def apply(self, resource: DesiredResource) -> None:
        manifest = resource.state or self.default_manifest(resource)
        path = self.resource_path(resource)
        headers = {
            **self.auth_headers(),
            "Content-Type": "application/merge-patch+json",
        }
        async with httpx.AsyncClient(
            timeout=KUBERNETES_API_TIMEOUT_SECONDS,
            verify=KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
        ) as client:
            response = await client.patch(
                f"{self.base_url()}{path}",
                headers=headers,
                json=manifest,
            )
            if response.status_code == 404:
                response = await client.post(
                    f"{self.base_url()}{self.collection_path(resource)}",
                    headers={**self.auth_headers(), "Content-Type": "application/json"},
                    json=manifest,
                )
            response.raise_for_status()

    async def observe(self, resource: DesiredResource) -> None:
        async with httpx.AsyncClient(
            timeout=KUBERNETES_API_TIMEOUT_SECONDS,
            verify=KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
        ) as client:
            response = await client.get(
                f"{self.base_url()}{self.resource_path(resource)}",
                headers=self.auth_headers(),
            )
            response.raise_for_status()

    def resource_path(self, resource: DesiredResource) -> str:
        if resource.kind == "ConfigMap":
            return f"/api/v1/namespaces/{resource.namespace}/configmaps/{resource.name}"
        return f"/apis/apps/v1/namespaces/{resource.namespace}/deployments/{resource.name}"

    def collection_path(self, resource: DesiredResource) -> str:
        if resource.kind == "ConfigMap":
            return f"/api/v1/namespaces/{resource.namespace}/configmaps"
        return f"/apis/apps/v1/namespaces/{resource.namespace}/deployments"

    def default_manifest(self, resource: DesiredResource) -> dict[str, object]:
        return {
            "apiVersion": "v1" if resource.kind == "ConfigMap" else "apps/v1",
            "kind": resource.kind,
            "metadata": {
                "name": resource.name,
                "namespace": resource.namespace,
            },
        }


class DesiredStateReconciler:
    def __init__(
        self,
        *,
        cluster_id: str,
        cluster_role: str,
        store: AgentControlStore,
        interval_seconds: int,
        resource_applier: ResourceApplier | None = None,
        reconciler_mode: str = DEFAULT_RECONCILER_MODE,
        argo_observer: ArgoObserver | None = None,
    ) -> None:
        if reconciler_mode not in RECONCILER_MODES:
            raise ValueError(
                f"reconciler_mode must be one of {sorted(RECONCILER_MODES)}: {reconciler_mode!r}"
            )
        self.cluster_id = cluster_id
        self.cluster_role = cluster_role
        self.store = store
        self.interval_seconds = interval_seconds
        self.resource_applier = resource_applier or KubernetesResourceClient()
        self.reconciler_mode = reconciler_mode
        self.argo_observer = argo_observer

    async def run(self, client: ManagementPlaneClient) -> None:
        while True:
            try:
                details = await self.reconcile_once()
                await client.report_reconcile_status(details)
            except Exception as exc:
                LOGGER.warning(
                    "desired_state_reconcile_failed",
                    extra={CONTEXT_KEY: {"cluster_id": self.cluster_id}},
                    exc_info=exc,
                )
            await asyncio.sleep(self.interval_seconds)

    async def reconcile_once(self, policy: AgentPolicy | None = None) -> dict[str, object]:
        policy = policy or self.store.load_reconcile_policy()
        if policy is None:
            return {
                "cluster_id": self.cluster_id,
                "generation": 0,
                "status": RECONCILE_UNCHANGED,
                "message": "no policy available",
                "details": {"resources": []},
            }

        results = []
        status = RECONCILE_UNCHANGED
        with TRACER.start_as_current_span("desired_state.reconcile") as span:
            span.attr("policy.generation", policy.generation)
            for resource in self.policy_resources(policy):
                result = await self.reconcile_resource(resource)
                results.append(result.__dict__)
                if result.status == RECONCILE_FAILED:
                    status = RECONCILE_FAILED
                    break
                elif result.status == RECONCILE_APPLIED and status != RECONCILE_FAILED:
                    status = RECONCILE_APPLIED

        details: dict[str, object] = {"resources": results}
        if self.reconciler_mode == RECONCILER_MODE_ARGOCD and self.argo_observer is not None:
            try:
                argocd = await self.argo_observer.snapshot()
                details["argocd"] = argocd
                applications = argocd.get("applications")
                if not isinstance(applications, dict) or applications.get("available") is not True:
                    status = RECONCILE_FAILED
                    argocd["error"] = "Argo CD Application observation unavailable"
            except Exception as exc:
                status = RECONCILE_FAILED
                details["argocd"] = {
                    "applications": unavailable_collection(),
                    "rollouts": unavailable_collection(),
                    "error": str(exc),
                }

        return {
            "cluster_id": self.cluster_id,
            "generation": policy.generation,
            "status": status,
            "message": f"reconciled {len(results)} resources",
            "details": details,
        }

    async def reconcile_resource(self, resource: DesiredResource) -> ReconcileResult:
        desired_hash = desired_resource_hash(resource)
        try:
            self.ensure_allowed(resource)
            if resource.action == "apply" and self.reconciler_mode == RECONCILER_MODE_ARGOCD:
                await self.resource_applier.observe(resource)
                result = self.result(
                    resource,
                    desired_hash,
                    RECONCILE_UNCHANGED,
                    "observed (argocd single-writer mode)",
                )
                self.store.save_reconcile_result(result)
                return result
            if (
                resource.action == "apply"
                and self.store.last_successful_resource_hash(resource.resource_id) == desired_hash
            ):
                result = self.result(resource, desired_hash, RECONCILE_UNCHANGED, "already applied")
                self.store.save_reconcile_result(result)
                return result
            if resource.action == "apply":
                await self.resource_applier.apply(resource)
                result = self.result(resource, desired_hash, RECONCILE_APPLIED, "applied")
            else:
                await self.resource_applier.observe(resource)
                result = self.result(resource, desired_hash, RECONCILE_UNCHANGED, "observed")
            self.store.save_reconcile_result(result)
            return result
        except Exception as exc:
            result = self.result(resource, desired_hash, RECONCILE_FAILED, str(exc))
            self.store.save_reconcile_result(result)
            return result

    def ensure_allowed(self, resource: DesiredResource) -> None:
        if resource.scope == "user-workload":
            raise PermissionError("user workload reconciliation is not enabled")
        expected_namespace = "management" if self.cluster_role == "management" else "target"
        if resource.namespace != expected_namespace:
            raise PermissionError(
                f"{self.cluster_role} agent cannot reconcile namespace {resource.namespace}"
            )
        if resource.kind == "ConfigMap":
            if resource.name not in {"target-agent-policy", TARGET_RUNTIME_CONFIG_NAME}:
                raise PermissionError("target-agent configmap control is name-scoped")
            if resource.name == TARGET_RUNTIME_CONFIG_NAME:
                self.ensure_runtime_image_patch(resource)
        if resource.scope == "target-agent" and resource.kind == "Deployment":
            if resource.name != "cluster-agent":
                raise PermissionError("target-agent deployment control is name-scoped")
        if resource.scope == "system" and resource.kind == "Deployment":
            raise PermissionError("system deployment reconciliation is not enabled")

    def ensure_runtime_image_patch(self, resource: DesiredResource) -> None:
        expected = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": TARGET_RUNTIME_CONFIG_NAME, "namespace": "target"},
        }
        if any(resource.state.get(key) != value for key, value in expected.items()):
            raise PermissionError("target runtime config patch identity is invalid")
        if set(resource.state) != {*expected, "data"}:
            raise PermissionError("target runtime config patch fields are not allowed")
        data = resource.state.get("data")
        if not isinstance(data, dict) or set(data) != {
            TARGET_AGENT_IMAGE_KEY,
            NODE_COLLECTOR_IMAGE_KEY,
        }:
            raise PermissionError("target runtime config patch is image-leaf scoped")
        target_image = require_target_image_digest(str(data[TARGET_AGENT_IMAGE_KEY]))
        collector_image = require_target_image_digest(str(data[NODE_COLLECTOR_IMAGE_KEY]))
        if target_image != collector_image:
            raise PermissionError("target runtime images must use one exact digest")

    def policy_resources(self, policy: AgentPolicy) -> Iterable[DesiredResource]:
        yield from policy.bootstrap.resources
        yield from policy.desired_state.resources

    def result(
        self,
        resource: DesiredResource,
        desired_hash: str,
        status: str,
        message: str,
    ) -> ReconcileResult:
        return ReconcileResult(
            resource_id=resource.resource_id,
            scope=resource.scope,
            kind=resource.kind,
            namespace=resource.namespace,
            name=resource.name,
            desired_hash=desired_hash,
            status=status,
            message=message,
        )
