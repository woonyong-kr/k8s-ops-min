from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kubernetes_api import count_not_ready_pods, pods_on_node
from prometheus_metrics import MetricSample


class MetricCollector(Protocol):
    # 실제 collector 가 아닌 타입 계약 — collector_name 과 collect(labels) 를 갖추면 충족.
    collector_name: str

    async def collect(self, labels: dict[str, str]) -> list[MetricSample]: ...


class NodeScopedKubernetesApi(Protocol):
    """Kubernetes read contract for a cluster-agent-managed node subworker."""

    async def list_pods_on_node(self, node_name: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class PodSummary:
    # Kubernetes Pod 데이터를 이 collector 담당 노드 범위 값으로 축약한 것.
    pod_count: int
    not_ready_pod_count: int


def collector_status_metric_sample(
    labels: dict[str, str],
    collector_name: str,
    has_error: bool,
) -> MetricSample:
    # 부분 수집 실패를 Prometheus 에서 볼 수 있도록 collector health 를 항상 노출.
    return MetricSample(
        name="node_collector_scrape_error",
        help="Whether node collector failed to read Kubernetes API data.",
        value=1 if has_error else 0,
        labels={**labels, "collector": collector_name},
    )


class PodMetricCollector:
    # Pod 관련 Kubernetes API 읽기를 담당하고 MetricSample 값으로 바로 변환함.
    collector_name = "pod"

    def __init__(self, kubernetes: NodeScopedKubernetesApi, node_name: str) -> None:
        self.kubernetes = kubernetes
        self.node_name = node_name

    async def collect_pod_summary(self) -> PodSummary:
        # API 서버에서 먼저 노드 범위를 제한하고 로컬 필터는 방어적으로 유지한다.
        pods_payload = await self.kubernetes.list_pods_on_node(self.node_name)
        node_pods = pods_on_node(pods_payload, self.node_name)
        return PodSummary(
            pod_count=len(node_pods),
            not_ready_pod_count=count_not_ready_pods(node_pods),
        )

    async def collect(self, labels: dict[str, str]) -> list[MetricSample]:
        summary = await self.collect_pod_summary()
        return [
            MetricSample(
                name="node_collector_node_pod_count",
                help="Pods scheduled on this Kubernetes node.",
                value=summary.pod_count,
                labels=labels,
            ),
            MetricSample(
                name="node_collector_node_not_ready_pod_count",
                help="Pods scheduled on this Kubernetes node that are not Ready.",
                value=summary.not_ready_pod_count,
                labels=labels,
            ),
        ]
