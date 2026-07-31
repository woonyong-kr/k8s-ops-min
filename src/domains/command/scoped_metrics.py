"""Build bounded, server-owned Prometheus queries for typed Resources subjects."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from packages.config.refresh_policies import integral_refresh_after_seconds
from packages.contracts.gateway.requests import AgentDebugQueryRequest
from packages.contracts.parity import ClusterScope, ResourceRef
from packages.contracts.scoped_metrics import (
    MetricCategory,
    MetricRefreshPolicyKey,
    ScopedMetricQueryRequest,
)

RANGE_SECONDS = {"15m": 900, "1h": 3_600, "6h": 21_600, "24h": 86_400}
RESOURCE_KINDS = {"Pod", "Node", "HorizontalPodAutoscaler"}
PROMETHEUS_CATEGORIES = {
    "cpu",
    "memory",
    "network_rx",
    "network_tx",
    "filesystem",
    "restarts",
    "hpa_current_replicas",
    "hpa_desired_replicas",
}
UNITS: dict[MetricCategory, str] = {
    "cpu": "cores",
    "memory": "bytes",
    "network_rx": "bytes_per_second",
    "network_tx": "bytes_per_second",
    "filesystem": "bytes",
    "restarts": "count",
    "volume_usage": "ratio",
    "hpa_current_replicas": "count",
    "hpa_desired_replicas": "count",
}


@dataclass(frozen=True)
class ScopedMetricIdentity:
    scope: ClusterScope
    resource: ResourceRef | None
    selectors: tuple[tuple[str, str], ...]
    refresh_policy_key: MetricRefreshPolicyKey
    supported: bool
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ScopedMetricPlan:
    category: MetricCategory
    unit: str
    payload: AgentDebugQueryRequest


def resolve_scoped_metric_identity(
    request: ScopedMetricQueryRequest,
    *,
    workspace_id: str,
    inventory_resource: dict[str, Any] | None,
) -> ScopedMetricIdentity:
    subject = request.subject
    if subject.kind == "cluster":
        return ScopedMetricIdentity(
            scope=ClusterScope(workspace_id=workspace_id, cluster_id=request.cluster_id),
            resource=None,
            selectors=(),
            refresh_policy_key="metrics_prometheus",
            supported=True,
        )
    if subject.kind == "namespace":
        return ScopedMetricIdentity(
            scope=ClusterScope(
                workspace_id=workspace_id,
                cluster_id=request.cluster_id,
                namespaces=(subject.namespace,),
            ),
            resource=None,
            selectors=(("namespace", subject.namespace),),
            refresh_policy_key="metrics_prometheus",
            supported=True,
        )
    if inventory_resource is None:
        raise LookupError("metric resource not found")
    cluster_id = str(inventory_resource.get("cluster_id") or "")
    if cluster_id != request.cluster_id:
        raise LookupError("metric resource not found")
    namespace = _optional_text(inventory_resource.get("namespace"))
    scope = ClusterScope(
        workspace_id=workspace_id,
        cluster_id=request.cluster_id,
        namespaces=(namespace,) if namespace else (),
    )
    uid = _optional_text(inventory_resource.get("uid"))
    kind = _required_text(inventory_resource.get("kind"))
    if uid is None:
        return ScopedMetricIdentity(
            scope=scope,
            resource=None,
            selectors=(),
            refresh_policy_key=("metrics_pvc" if subject.kind == "pvc" else "metrics_prometheus"),
            supported=False,
            unavailable_reason="resource_uid_unavailable",
        )
    resource = _resource_ref(inventory_resource, uid=uid, namespace=namespace, kind=kind)
    if subject.kind == "pvc":
        supported = kind == "PersistentVolumeClaim" and namespace is not None
        return ScopedMetricIdentity(
            scope=scope,
            resource=resource,
            selectors=(
                ("namespace", namespace or ""),
                ("persistentvolumeclaim", resource.name),
            ),
            refresh_policy_key="metrics_pvc",
            supported=supported,
            unavailable_reason=None if supported else "pvc_metrics_unsupported",
        )
    supported = kind in RESOURCE_KINDS
    selectors = _resource_selectors(resource) if supported else ()
    return ScopedMetricIdentity(
        scope=scope,
        resource=resource,
        selectors=selectors,
        refresh_policy_key="metrics_prometheus",
        supported=supported,
        unavailable_reason=None if supported else "resource_metrics_unsupported",
    )


def build_scoped_metric_plans(
    request: ScopedMetricQueryRequest,
    identity: ScopedMetricIdentity,
    *,
    now: float | None = None,
) -> tuple[tuple[ScopedMetricPlan, ...], tuple[MetricCategory, ...]]:
    if not identity.supported:
        return (), request.categories
    supported_categories = (
        {"volume_usage"} if identity.refresh_policy_key == "metrics_pvc" else PROMETHEUS_CATEGORIES
    )
    supported = tuple(
        category for category in request.categories if category in supported_categories
    )
    unsupported = tuple(
        category for category in request.categories if category not in supported_categories
    )
    range_seconds = RANGE_SECONDS[request.range]
    # The provider preserves at most 40 values per series. Keep each aggregate
    # below that boundary so ordinary range observations never arrive silently
    # truncated at the browser contract.
    step_seconds = max(1, min(3_600, range_seconds // 30))
    refresh_seconds = integral_refresh_after_seconds(identity.refresh_policy_key)
    observation_bucket = int((time.time() if now is None else now) // refresh_seconds)
    plans: list[ScopedMetricPlan] = []
    for category in supported:
        promql = _promql(category, identity.selectors)
        subject_digest = hashlib.sha256(
            json.dumps(
                {
                    "cluster_id": request.cluster_id,
                    "subject": request.subject.model_dump(mode="json"),
                    "category": category,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        name = f"resource_{category}_{subject_digest}_{observation_bucket}"
        plans.append(
            ScopedMetricPlan(
                category=category,
                unit=UNITS[category],
                payload=AgentDebugQueryRequest(
                    cluster_id=request.cluster_id,
                    query={
                        "source": "prometheus",
                        "name": name,
                        "description": "Server-owned scoped resource metric observation.",
                        "query": promql,
                        "range_seconds": range_seconds,
                        "step_seconds": step_seconds,
                        # The target ignores unknown metadata. The gateway includes this
                        # server-owned cadence bucket in command idempotency so a visible
                        # refresh cannot duplicate a query inside the declared interval.
                        "observation_bucket": observation_bucket,
                    },
                    reason="scoped resource metric observation",
                ),
            )
        )
    return tuple(plans), unsupported


def _promql(category: MetricCategory, selectors: tuple[tuple[str, str], ...]) -> str:
    selector = _selector(selectors)
    if category == "cpu":
        return f"sum(rate(container_cpu_usage_seconds_total{_with_base(selector)}[5m]))"
    if category == "memory":
        return f"sum(container_memory_working_set_bytes{_with_base(selector)})"
    if category == "network_rx":
        return f"sum(rate(container_network_receive_bytes_total{selector}[5m]))"
    if category == "network_tx":
        return f"sum(rate(container_network_transmit_bytes_total{selector}[5m]))"
    if category == "filesystem":
        return f"sum(container_fs_usage_bytes{_with_base(selector)})"
    if category == "restarts":
        return f"sum(kube_pod_container_status_restarts_total{selector})"
    if category == "volume_usage":
        used = f"kubelet_volume_stats_used_bytes{selector}"
        capacity = f"kubelet_volume_stats_capacity_bytes{selector}"
        return f"({used} / clamp_min({capacity}, 1))"
    if category == "hpa_current_replicas":
        return f"kube_horizontalpodautoscaler_status_current_replicas{selector}"
    if category == "hpa_desired_replicas":
        return f"kube_horizontalpodautoscaler_status_desired_replicas{selector}"
    raise ValueError(f"unsupported metric category: {category}")


def _with_base(selector: str) -> str:
    values = ['container!=""', 'image!=""']
    if selector != "{}":
        values.extend(selector[1:-1].split(","))
    return "{" + ",".join(values) + "}"


def _selector(values: tuple[tuple[str, str], ...]) -> str:
    return "{" + ",".join(f"{key}={json.dumps(value)}" for key, value in values) + "}"


def _resource_selectors(resource: ResourceRef) -> tuple[tuple[str, str], ...]:
    if resource.kind == "Pod":
        return tuple(
            (key, value)
            for key, value in (("namespace", resource.namespace), ("pod", resource.name))
            if value is not None
        )
    if resource.kind == "Node":
        return (("node", resource.name),)
    if resource.kind == "HorizontalPodAutoscaler":
        return tuple(
            (key, value)
            for key, value in (
                ("horizontalpodautoscaler", resource.name),
                ("namespace", resource.namespace),
            )
            if value is not None
        )
    raise ValueError(f"unsupported metric resource kind: {resource.kind}")


def _resource_ref(
    row: dict[str, Any],
    *,
    uid: str,
    namespace: str | None,
    kind: str,
) -> ResourceRef:
    api_version = _required_text(row.get("api_version"))
    if "/" in api_version:
        api_group, version = api_version.split("/", 1)
    else:
        api_group, version = "", api_version
    return ResourceRef(
        api_group=api_group,
        version=version,
        kind=kind,
        namespace=namespace,
        name=_required_text(row.get("name")),
        uid=uid,
    )


def _required_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("metric resource identity is incomplete")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
