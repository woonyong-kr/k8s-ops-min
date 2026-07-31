from __future__ import annotations

from collections.abc import Mapping

import httpx
from kubernetes_api import (
    kubernetes_api_base_url,
    kubernetes_client,
    kubernetes_headers,
    service_account_token,
)
from node_collector_spec import node_collector_daemonset

from packages.config.settings import env
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.target import (
    NODE_COLLECTOR_READ_CLUSTER_ROLE_BINDING_NAME,
    NODE_COLLECTOR_READ_CLUSTER_ROLE_NAME,
    NODE_COLLECTOR_SERVICE_ACCOUNT_NAME,
    require_target_image_digest,
)


class NodeCollectorManagerConfig:
    NODE_COLLECTOR_ENABLED_ENV = "NODE_COLLECTOR_ENABLED"
    NODE_COLLECTOR_IMAGE_ENV = "NODE_COLLECTOR_IMAGE"
    NODE_COLLECTOR_NAMESPACE_ENV = "NODE_COLLECTOR_NAMESPACE"
    NODE_COLLECTOR_NAME = "optional-node-collector"
    NODE_COLLECTOR_APP_LABEL = "optional-node-collector"
    NODE_COLLECTOR_CONTAINER_NAME = "node-collector"
    NODE_COLLECTOR_DEFAULT_IMAGE = ""
    NODE_COLLECTOR_DEFAULT_NAMESPACE = "target"
    NODE_COLLECTOR_PORT_ENV = "NODE_COLLECTOR_PORT"
    NODE_COLLECTOR_COLLECT_INTERVAL_SECONDS_ENV = "NODE_COLLECTOR_COLLECT_INTERVAL_SECONDS"
    # 수집기 포트/수집 주기 — 클러스터 사정에 맞춰 env 로 오버라이드 가능함.
    # env 미설정 시 기존 기본값(9100/15초)과 동일함(배포 호환).
    NODE_COLLECTOR_PORT = int(env(NODE_COLLECTOR_PORT_ENV, "9100"))
    NODE_COLLECTOR_COLLECT_INTERVAL_SECONDS = int(
        env(NODE_COLLECTOR_COLLECT_INTERVAL_SECONDS_ENV, "15")
    )
    NODE_COLLECTOR_CREATED_MESSAGE = "node collector daemonset created"
    NODE_COLLECTOR_PATCHED_MESSAGE = "node collector daemonset reconciled"
    NODE_COLLECTOR_PENDING_MESSAGE = "node collector rollout pending exact image digest"
    NODE_COLLECTOR_IDENTITY_PENDING_MESSAGE = "node collector identity requires administrator apply"
    NODE_COLLECTOR_DRY_RUN_MESSAGE = "kubernetes api not configured; node collector dry-run only"
    NODE_COLLECTOR_DISABLED_MESSAGE = "node collector reconcile disabled"
    NODE_COLLECTOR_IMAGE_REQUIRED_MESSAGE = "node collector image is required"
    NODE_COLLECTOR_MANAGED_BY_LABEL = "ops.service/managed-by"
    NODE_COLLECTOR_MANAGED_BY_VALUE = "cluster-agent"


class NodeCollectorManager:
    """target cluster 노드마다 node collector DaemonSet 유지.

    인계 기준: target registration은 cluster-agent 설치까지만 담당.
    이후 collector rollout과 drift correction은 target-agent 경계에서 처리.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        image: str,
        namespace: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enabled = enabled
        self.image = image
        self.namespace = namespace
        self.transport = transport

    @classmethod
    def from_env(cls, transport: httpx.AsyncBaseTransport | None = None) -> NodeCollectorManager:
        return cls(
            enabled=truthy(env(NodeCollectorManagerConfig.NODE_COLLECTOR_ENABLED_ENV, "true")),
            image=env(
                NodeCollectorManagerConfig.NODE_COLLECTOR_IMAGE_ENV,
                NodeCollectorManagerConfig.NODE_COLLECTOR_DEFAULT_IMAGE,
            ),
            namespace=env(
                NodeCollectorManagerConfig.NODE_COLLECTOR_NAMESPACE_ENV,
                NodeCollectorManagerConfig.NODE_COLLECTOR_DEFAULT_NAMESPACE,
            ),
            transport=transport,
        )

    async def reconcile(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, NodeCollectorManagerConfig.NODE_COLLECTOR_DISABLED_MESSAGE
        if not self.image:
            return False, NodeCollectorManagerConfig.NODE_COLLECTOR_IMAGE_REQUIRED_MESSAGE
        image = require_target_image_digest(self.image)
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            return False, NodeCollectorManagerConfig.NODE_COLLECTOR_DRY_RUN_MESSAGE

        daemonset = self.daemonset()
        collection_url = f"{base_url}/apis/apps/v1/namespaces/{self.namespace}/daemonsets"
        resource_url = f"{collection_url}/{NodeCollectorManagerConfig.NODE_COLLECTOR_NAME}"
        async with kubernetes_client(self.transport) as client:
            identity_urls = (
                (
                    f"{base_url}/api/v1/namespaces/{self.namespace}/serviceaccounts/"
                    f"{NODE_COLLECTOR_SERVICE_ACCOUNT_NAME}"
                ),
                (
                    f"{base_url}/apis/rbac.authorization.k8s.io/v1/clusterroles/"
                    f"{NODE_COLLECTOR_READ_CLUSTER_ROLE_NAME}"
                ),
                (
                    f"{base_url}/apis/rbac.authorization.k8s.io/v1/clusterrolebindings/"
                    f"{NODE_COLLECTOR_READ_CLUSTER_ROLE_BINDING_NAME}"
                ),
            )
            for identity_url in identity_urls:
                identity = await client.get(identity_url, headers=kubernetes_headers(token))
                if identity.status_code == 404:
                    return (
                        False,
                        NodeCollectorManagerConfig.NODE_COLLECTOR_IDENTITY_PENDING_MESSAGE,
                    )
                identity.raise_for_status()
            current = await client.get(resource_url, headers=kubernetes_headers(token))
            if current.status_code == 404:
                response = await client.post(
                    collection_url,
                    json=daemonset,
                    headers=kubernetes_headers(token, "application/json"),
                )
                message = NodeCollectorManagerConfig.NODE_COLLECTOR_CREATED_MESSAGE
            else:
                current.raise_for_status()
                response = await client.patch(
                    resource_url,
                    json={"metadata": daemonset["metadata"], "spec": daemonset["spec"]},
                    headers=kubernetes_headers(token, "application/strategic-merge-patch+json"),
                )
                message = NodeCollectorManagerConfig.NODE_COLLECTOR_PATCHED_MESSAGE
            response.raise_for_status()
            pods = await client.get(
                f"{base_url}/api/v1/namespaces/{self.namespace}/pods",
                params={
                    "labelSelector": f"app={NodeCollectorManagerConfig.NODE_COLLECTOR_APP_LABEL}"
                },
                headers=kubernetes_headers(token),
            )
            pods.raise_for_status()
            if not node_collector_rollout_ready(response.json(), pods.json(), image):
                return False, NodeCollectorManagerConfig.NODE_COLLECTOR_PENDING_MESSAGE
        return True, message

    def daemonset(self) -> JsonObject:
        return node_collector_daemonset(
            name=NodeCollectorManagerConfig.NODE_COLLECTOR_NAME,
            namespace=self.namespace,
            image=self.image,
            app_label=NodeCollectorManagerConfig.NODE_COLLECTOR_APP_LABEL,
            managed_by_label=NodeCollectorManagerConfig.NODE_COLLECTOR_MANAGED_BY_LABEL,
            managed_by_value=NodeCollectorManagerConfig.NODE_COLLECTOR_MANAGED_BY_VALUE,
            container_name=NodeCollectorManagerConfig.NODE_COLLECTOR_CONTAINER_NAME,
            port=NodeCollectorManagerConfig.NODE_COLLECTOR_PORT,
            collect_interval_seconds=(
                NodeCollectorManagerConfig.NODE_COLLECTOR_COLLECT_INTERVAL_SECONDS
            ),
        )


def truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def node_collector_rollout_ready(
    daemonset: object,
    pods: object,
    expected_image: str,
) -> bool:
    """Require desired DaemonSet spec and running Pod imageID to share one digest."""

    if not isinstance(daemonset, Mapping) or not isinstance(pods, Mapping):
        return False
    expected = require_target_image_digest(expected_image)
    if daemonset_image(daemonset) != expected:
        return False
    metadata = mapping(daemonset.get("metadata"))
    status = mapping(daemonset.get("status"))
    generation = integer(metadata.get("generation"))
    observed_generation = integer(status.get("observedGeneration"))
    desired = integer(status.get("desiredNumberScheduled"))
    updated = integer(status.get("updatedNumberScheduled"))
    ready = integer(status.get("numberReady"))
    unavailable = integer(status.get("numberUnavailable"), default=0)
    if (
        generation is None
        or observed_generation is None
        or desired is None
        or updated is None
        or ready is None
        or unavailable is None
        or observed_generation < generation
        or updated != desired
        or ready != desired
        or unavailable != 0
    ):
        return False
    if desired == 0:
        return True
    items = pods.get("items")
    if not isinstance(items, list):
        return False
    exact_ready = sum(
        pod_uses_exact_image(item, expected) for item in items if isinstance(item, Mapping)
    )
    return exact_ready >= desired


def daemonset_image(daemonset: Mapping[object, object]) -> str:
    spec = mapping(daemonset.get("spec"))
    template = mapping(spec.get("template"))
    pod_spec = mapping(template.get("spec"))
    containers = pod_spec.get("containers")
    if not isinstance(containers, list):
        return ""
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        if container.get("name") == NodeCollectorManagerConfig.NODE_COLLECTOR_CONTAINER_NAME:
            image = container.get("image")
            return image if isinstance(image, str) else ""
    return ""


def pod_uses_exact_image(pod: Mapping[object, object], expected_image: str) -> bool:
    expected = require_target_image_digest(expected_image)
    pod_spec = mapping(pod.get("spec"))
    spec_containers = pod_spec.get("containers")
    if not isinstance(spec_containers, list):
        return False
    spec_image = ""
    for container in spec_containers:
        if not isinstance(container, Mapping):
            continue
        if container.get("name") != NodeCollectorManagerConfig.NODE_COLLECTOR_CONTAINER_NAME:
            continue
        image = container.get("image")
        spec_image = image if isinstance(image, str) else ""
        break
    if spec_image != expected:
        return False

    status = mapping(pod.get("status"))
    container_statuses = status.get("containerStatuses")
    if not isinstance(container_statuses, list):
        return False
    expected_digest = expected.rsplit("@", 1)[1]
    for container in container_statuses:
        if not isinstance(container, Mapping):
            continue
        if container.get("name") != NodeCollectorManagerConfig.NODE_COLLECTOR_CONTAINER_NAME:
            continue
        image_id = container.get("imageID")
        return (
            container.get("ready") is True
            and isinstance(image_id, str)
            and image_id_has_exact_digest(image_id, expected_digest)
        )
    return False


def image_id_has_exact_digest(image_id: str, expected_digest: str) -> bool:
    normalized = image_id.strip()
    return (
        normalized == expected_digest
        or normalized.endswith(f"@{expected_digest}")
        or normalized.endswith(f"://{expected_digest}")
    )


def mapping(value: object) -> Mapping[object, object]:
    return value if isinstance(value, Mapping) else {}


def integer(value: object, *, default: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value
