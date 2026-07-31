from __future__ import annotations

from pathlib import Path

import httpx

from packages.config.settings import env

DEFAULT_KUBERNETES_SERVICE_HOST = "kubernetes.default.svc"
DEFAULT_KUBERNETES_SERVICE_PORT = "443"
KUBERNETES_SERVICE_HOST_ENV = "KUBERNETES_SERVICE_HOST"
KUBERNETES_SERVICE_PORT_ENV = "KUBERNETES_SERVICE_PORT_HTTPS"
KUBERNETES_SERVICEACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBERNETES_SERVICEACCOUNT_TOKEN_PATH = f"{KUBERNETES_SERVICEACCOUNT_DIR}/token"
KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH = f"{KUBERNETES_SERVICEACCOUNT_DIR}/ca.crt"
KUBERNETES_API_TIMEOUT_SECONDS_ENV = "KUBERNETES_API_TIMEOUT_SECONDS"  # k8s API 타임아웃 초(기본 5)
KUBERNETES_API_TIMEOUT_SECONDS = int(env(KUBERNETES_API_TIMEOUT_SECONDS_ENV, "5"))
POD_NODE_FIELD_SELECTOR = "spec.nodeName"
NODE_SCOPE_REQUIRED = "node_name is required for bounded pod collection"

# collector 전용 payload 모델이 굳기 전까지 helper 시그니처를 짧게 유지하려는 로컬 alias.
JsonObject = dict[str, object]


class KubernetesApiClient:
    # Pod ServiceAccount 로 in-cluster Kubernetes API 호출.
    def base_url(self) -> str:
        host = env(KUBERNETES_SERVICE_HOST_ENV, DEFAULT_KUBERNETES_SERVICE_HOST)
        port = env(KUBERNETES_SERVICE_PORT_ENV, DEFAULT_KUBERNETES_SERVICE_PORT)
        return f"https://{host}:{port}"

    def auth_headers(self) -> dict[str, str]:
        token = Path(KUBERNETES_SERVICEACCOUNT_TOKEN_PATH).read_text(encoding="utf-8").strip()
        return {"Authorization": f"Bearer {token}"}

    async def list_pods_on_node(self, node_name: str) -> JsonObject:
        """Return only pods assigned to one exact node.

        The node collector is a cluster-agent-managed bounded subworker.  It
        must never download a cluster-wide PodList and filter it locally.
        """

        normalized_node_name = node_name.strip()
        if not normalized_node_name:
            raise ValueError(NODE_SCOPE_REQUIRED)
        async with httpx.AsyncClient(
            timeout=KUBERNETES_API_TIMEOUT_SECONDS,
            verify=KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
        ) as client:
            response = await client.get(
                f"{self.base_url()}/api/v1/pods",
                headers=self.auth_headers(),
                params={"fieldSelector": f"{POD_NODE_FIELD_SELECTOR}={normalized_node_name}"},
            )
            response.raise_for_status()
            return response.json()


def pods_on_node(pods_payload: JsonObject, node_name: str) -> list[JsonObject]:
    # Pod 목록은 최상위 "items" 필드에 담김: {"kind": "PodList", "items": [{...pod...}, ...]}
    items = pods_payload.get("items", [])
    if not isinstance(items, list):
        return []

    node_pods = []
    for pod in items:
        if not isinstance(pod, dict):
            continue

        spec = pod.get("spec", {})
        if not isinstance(spec, dict):
            continue

        # spec.nodeName 은 scheduler 가 노드를 배정한 뒤에 채워짐.
        if spec.get("nodeName") == node_name:
            node_pods.append(pod)

    return node_pods


def is_pod_ready(pod: JsonObject) -> bool:
    # readiness 는 spec 이 아닌 status.conditions 에 있음.
    # {"type": "Ready", "status": "True"} 가 있어야 Ready.
    status = pod.get("status", {})
    if not isinstance(status, dict):
        return False

    conditions = status.get("conditions", [])
    if not isinstance(conditions, list):
        return False

    for condition in conditions:
        if not isinstance(condition, dict):
            continue

        if condition.get("type") == "Ready":
            return condition.get("status") == "True"

    # Ready condition 이 없거나 손상되면 not ready 로 간주.
    return False


def count_not_ready_pods(pods: list[JsonObject]) -> int:
    return sum(1 for pod in pods if not is_pod_ready(pod))
