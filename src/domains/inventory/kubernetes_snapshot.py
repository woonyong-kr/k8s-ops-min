"""Translate target-agent Kubernetes evidence into inventory read-model payloads."""

from __future__ import annotations

from collections import Counter
from typing import Any

from domains.inventory.coverage import (
    COLLECTION_COVERAGE_SUMMARY_KEY,
    LEGACY_LIVE_INVENTORY_COLLECTIONS,
    SCOPED_INVENTORY_COLLECTIONS,
    kubernetes_collection_coverage,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.kubernetes_provider import normalized_detected_provider


def kubernetes_evidence_to_inventory_snapshot(
    kubernetes: JsonObject,
    *,
    cluster_id: str,
    agent_id: str,
) -> JsonObject:
    cluster = _mapping(kubernetes.get("cluster"))
    collected_at = cluster.get("collected_at")
    resources = [
        *(_workload_resource(item) for item in _items(kubernetes, "workloads")),
        *(_workload_revision_resource(item) for item in _items(kubernetes, "workload_revisions")),
        *(_pod_resource(item) for item in _items(kubernetes, "pods")),
        *(_node_resource(item, _items(kubernetes, "pods")) for item in _items(kubernetes, "nodes")),
        *(_service_resource(item) for item in _items(kubernetes, "services")),
        *(_ingress_resource(item) for item in _items(kubernetes, "ingresses")),
        *(_resource_quota_resource(item) for item in _items(kubernetes, "resourcequotas")),
        *(_custom_resource(item) for item in _items(kubernetes, "custom_resources")),
        *(
            _event_resource(item, collected_at=collected_at)
            for item in _items(kubernetes, "events")
        ),
        *(_endpoint_resource(item) for item in _items(kubernetes, "endpoints")),
    ]
    resources_complete = _resources_complete(kubernetes)
    summary = _summary(kubernetes, resources_complete=resources_complete)
    return {
        "cluster_id": cluster_id,
        "agent_id": agent_id,
        "source": "cluster-agent:kubernetes",
        "collected_at": cluster.get("collected_at"),
        # Destructive replacement is safe only when every provider query completed and the
        # agent did not truncate any collection. Partial evidence must preserve prior rows.
        "replace": resources_complete,
        "resources": resources,
        "summary": summary,
        "health": {
            "status": "healthy" if resources else "empty",
            "provider_status": _mapping(kubernetes.get("provider_status")),
        },
        "usage": _usage_rollup(kubernetes),
    }


def _usage_rollup(kubernetes: JsonObject) -> JsonObject:
    """스냅샷 시점의 실측 활용 롤업 — cluster_usage_samples 시계열의 데이터 원천.

    agent 가 실제로 관측한 pod phase·재시작 수·node ready 만 집계한다(합성 값 금지).
    관측 대상이 하나도 없으면 빈 dict — 저장소가 usage 행을 만들지 않는다(기존 동작).
    """
    pods = _items(kubernetes, "pods")
    nodes = _items(kubernetes, "nodes")
    if not pods and not nodes:
        return {}
    phases = Counter(_text(pod.get("phase"), "Unknown") for pod in pods)
    usage = {
        "pod_total": len(pods),
        "pod_running": phases.get("Running", 0),
        "pod_pending": phases.get("Pending", 0),
        "pod_failed": phases.get("Failed", 0),
        "restart_total": sum(int(pod.get("restart_total") or 0) for pod in pods),
        "node_total": len(nodes),
        "node_ready": sum(1 for node in nodes if bool(node.get("ready"))),
    }
    pod_usage = _pod_usage(pods)
    node_usage = _node_usage(nodes)
    if pod_usage:
        usage["pods"] = pod_usage
    if node_usage:
        usage["nodes"] = node_usage

    cpu_pct = _cluster_pct(nodes, "cpu_mcores", "cpu_ratio")
    mem_pct = _cluster_pct(nodes, "mem_mib", "mem_ratio")
    if cpu_pct is not None:
        usage["cpu_pct"] = cpu_pct
    if mem_pct is not None:
        usage["mem_pct"] = mem_pct
    return usage


def _pod_usage(pods: list[JsonObject]) -> JsonObject:
    usage: JsonObject = {}
    for pod in pods:
        namespace = _text(pod.get("namespace"), "default")
        name = _text(pod.get("name"))
        if not name:
            continue
        payload: JsonObject = {}
        uid = pod.get("uid")
        if isinstance(uid, str) and uid.strip():
            payload["uid"] = uid
        for source_key, target_key in (
            ("cpu_mcores", "cpu_mcores"),
            ("mem_mib", "mem_mib"),
            ("cpu_request_mcores", "cpu_request_mcores"),
            ("cpu_limit_mcores", "cpu_limit_mcores"),
            ("mem_request_mib", "mem_request_mib"),
            ("mem_limit_mib", "mem_limit_mib"),
        ):
            value = _float_or_none(pod.get(source_key))
            if value is not None:
                payload[target_key] = value
        for key in ("metrics_observed_at", "metrics_window"):
            value = pod.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value
        container_metrics = pod.get("container_metrics")
        if isinstance(container_metrics, list) and all(
            isinstance(item, dict) for item in container_metrics
        ):
            payload["container_metrics"] = [dict(item) for item in container_metrics]
            payload["container_metrics_complete"] = pod.get("container_metrics_complete") is True
        if payload:
            usage[f"{namespace}/{name}"] = payload
    return usage


def _node_usage(nodes: list[JsonObject]) -> JsonObject:
    usage: JsonObject = {}
    for node in nodes:
        name = _text(node.get("name"))
        if not name:
            continue
        payload: JsonObject = {}
        for source_key, target_key in (
            ("cpu_mcores", "cpu_mcores"),
            ("mem_mib", "mem_mib"),
            ("cpu_ratio", "cpu_ratio"),
            ("mem_ratio", "mem_ratio"),
        ):
            value = _float_or_none(node.get(source_key))
            if value is not None:
                payload[target_key] = value
        for key in ("metrics_observed_at", "metrics_window"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value
        if payload:
            if "cpu_ratio" in payload:
                payload["cpu_pct"] = round(float(payload["cpu_ratio"]) * 100, 1)
            if "mem_ratio" in payload:
                payload["mem_pct"] = round(float(payload["mem_ratio"]) * 100, 1)
            usage[name] = payload
    return usage


def _cluster_pct(nodes: list[JsonObject], usage_key: str, ratio_key: str) -> float | None:
    observed = [
        (_float_or_none(node.get(usage_key)), _float_or_none(node.get(ratio_key))) for node in nodes
    ]
    ratios = [ratio for value, ratio in observed if value is not None and ratio is not None]
    if not ratios:
        return None
    return round(sum(ratios) / len(ratios) * 100, 1)


def _mapping(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _labels(item: JsonObject) -> JsonObject:
    return _mapping(item.get("labels"))


def _items(payload: JsonObject, key: str) -> list[JsonObject]:
    value = payload.get(key)
    return [dict(item) for item in value] if isinstance(value, list) else []


def _text(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _health(ok: bool) -> str:
    return "healthy" if ok else "degraded"


def _workload_resource(item: JsonObject) -> JsonObject:
    desired = int(item.get("desired_replicas") or 0)
    ready = int(item.get("ready_replicas") or 0)
    kind = _text(item.get("kind"), "Workload")
    failed = int(item.get("failed") or 0)
    succeeded = int(item.get("succeeded") or 0)
    active = int(item.get("active") or 0)
    completions = max(1, int(item.get("completions") or 1))
    if kind == "Job":
        status = (
            "Running"
            if active > 0
            else "Failed"
            if failed > 0
            else ("Succeeded" if succeeded >= completions else "Pending")
        )
        healthy = failed == 0
    else:
        status = f"{ready}/{desired}"
        healthy = desired == 0 or ready >= desired
    return {
        "resource_type": "workload",
        "api_version": _text(item.get("api_version"), "apps/v1"),
        "kind": kind,
        "namespace": _text(item.get("namespace"), "default"),
        "name": _text(item.get("name"), kind.lower()),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": status,
        "health": _health(healthy),
        "labels": _labels(item),
        "summary": item,
        "raw": item,
    }


def _workload_revision_resource(item: JsonObject) -> JsonObject:
    return {
        "resource_type": "workload_revision",
        "api_version": _text(item.get("api_version"), "apps/v1"),
        "kind": _text(item.get("kind"), "ControllerRevision"),
        "namespace": _text(item.get("namespace"), "default"),
        "name": _text(item.get("name"), "revision"),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": _text(item.get("revision"), "observed"),
        "health": "healthy",
        "labels": {},
        "summary": {
            "owner_kind": item.get("owner_kind"),
            "owner_name": item.get("owner_name"),
            "owner_uid": item.get("owner_uid"),
            "owner_references_complete": item.get("owner_references_complete") is True,
            "revision": item.get("revision"),
            "created_at": item.get("created_at"),
        },
        "raw": item,
    }


def _pod_resource(item: JsonObject) -> JsonObject:
    phase = _text(item.get("phase"), "Unknown")
    waiting = item.get("waiting_reasons") if isinstance(item.get("waiting_reasons"), list) else []
    containers = item.get("containers") if isinstance(item.get("containers"), list) else []
    conditions = item.get("conditions") if isinstance(item.get("conditions"), list) else []
    ready_condition = next(
        (
            condition
            for condition in conditions
            if isinstance(condition, dict) and condition.get("type") == "Ready"
        ),
        None,
    )
    ready = ready_condition is None or str(ready_condition.get("status")) == "True"
    return {
        "resource_type": "pod",
        "api_version": "v1",
        "kind": "Pod",
        "namespace": _text(item.get("namespace"), "default"),
        "name": _text(item.get("name"), "pod"),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": phase,
        "health": _health(phase == "Running" and not waiting and ready),
        "labels": _labels(item),
        "summary": {
            **item,
            "image": _first_container_image(containers),
        },
        "raw": item,
    }


def _node_resource(item: JsonObject, pods: list[JsonObject]) -> JsonObject:
    name = _text(item.get("name"), "node")
    ready = bool(item.get("ready"))
    return {
        "resource_type": "node",
        "api_version": "v1",
        "kind": "Node",
        "namespace": None,
        "name": name,
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": "Ready" if ready else "NotReady",
        "health": _health(ready),
        "labels": _labels(item),
        "summary": {
            **item,
            "pod_count": _scheduled_pod_count(pods, name),
        },
        "raw": item,
    }


def _service_resource(item: JsonObject) -> JsonObject:
    return {
        "resource_type": "service",
        "api_version": "v1",
        "kind": "Service",
        "namespace": _text(item.get("namespace"), "default"),
        "name": _text(item.get("name"), "service"),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": _text(item.get("type"), "Service"),
        "health": "healthy",
        "labels": _labels(item),
        "summary": item,
        "raw": item,
    }


def _ingress_resource(item: JsonObject) -> JsonObject:
    hosts = item.get("hosts") if isinstance(item.get("hosts"), list) else []
    external_hosts = (
        item.get("external_hosts") if isinstance(item.get("external_hosts"), list) else []
    )
    return {
        "resource_type": "ingress",
        "api_version": "networking.k8s.io/v1",
        "kind": "Ingress",
        "namespace": _text(item.get("namespace"), "default"),
        "name": _text(item.get("name"), "ingress"),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": "Address assigned" if external_hosts else "Pending",
        "health": "healthy" if hosts else "unknown",
        "labels": _labels(item),
        "summary": item,
        "raw": item,
    }


def _resource_quota_resource(item: JsonObject) -> JsonObject:
    return {
        "resource_type": "resourcequota",
        "api_version": "v1",
        "kind": "ResourceQuota",
        "namespace": _text(item.get("namespace")),
        "name": _text(item.get("name")),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": "Observed",
        "health": "healthy",
        "labels": _labels(item),
        "summary": {
            "hard": _mapping(item.get("hard")),
            "used": _mapping(item.get("used")),
        },
        "raw": item,
    }


def _custom_resource(item: JsonObject) -> JsonObject:
    raw = _mapping(item.get("raw"))
    raw_status = _mapping(raw.get("status"))
    sync = _mapping(raw_status.get("sync"))
    provider_health = _mapping(raw_status.get("health"))
    status_value = (
        _text(sync.get("status"))
        or _text(raw_status.get("phase"))
        or _text(provider_health.get("status"))
        or "Observed"
    )
    health_value = _text(provider_health.get("status")).casefold()
    health = (
        "healthy"
        if health_value in {"healthy", "ready", "succeeded", "true"}
        else "degraded"
        if health_value
        else "unknown"
    )
    return {
        "resource_type": "custom_resource",
        "api_version": _text(item.get("api_version")),
        "kind": _text(item.get("kind")),
        "namespace": item.get("namespace"),
        "name": _text(item.get("name")),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": status_value,
        "health": health,
        "labels": _labels(item),
        "annotations": _mapping(item.get("annotations")),
        "summary": {key: value for key, value in item.items() if key != "raw"},
        "raw": raw,
    }


def _event_resource(item: JsonObject, *, collected_at: object = None) -> JsonObject:
    name = _text(item.get("uid")) or ":".join(
        [
            _text(item.get("namespace"), "default"),
            _text(item.get("involved_kind"), "Object"),
            _text(item.get("involved_name"), "unknown"),
            _text(item.get("reason"), "event"),
        ]
    )
    event_type = _text(item.get("type"), "Normal")
    return {
        "resource_type": "event",
        "api_version": "v1",
        "kind": "Event",
        "namespace": _text(item.get("namespace"), "default"),
        "name": name,
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": event_type,
        "health": "degraded" if event_type.lower() == "warning" else "healthy",
        "labels": _labels(item),
        "summary": {**item, "collected_at": collected_at},
        "raw": item,
    }


def _endpoint_resource(item: JsonObject) -> JsonObject:
    return {
        "resource_type": "endpoint",
        "api_version": "discovery.k8s.io/v1",
        "kind": "EndpointSlice",
        "namespace": _text(item.get("namespace"), "default"),
        "name": _text(item.get("name"), "endpoint"),
        "uid": item.get("uid"),
        "resource_version": item.get("resource_version"),
        "status": _text(item.get("address_type"), "unknown"),
        "health": "healthy",
        "labels": _labels(item),
        "summary": item,
        "raw": item,
    }


def _first_container_image(containers: list[Any]) -> str:
    for container in containers:
        if isinstance(container, dict) and container.get("image"):
            return str(container["image"])
    return ""


def _summary(kubernetes: JsonObject, *, resources_complete: bool) -> JsonObject:
    pods = _items(kubernetes, "pods")
    nodes = _items(kubernetes, "nodes")
    services = _items(kubernetes, "services")
    phases = Counter(_text(pod.get("phase"), "Unknown") for pod in pods)
    namespaces = sorted(
        {
            _text(item.get("namespace"))
            for key in (
                "pods",
                "workloads",
                "services",
                "ingresses",
                "events",
                "endpoints",
                "resourcequotas",
                "custom_resources",
            )
            for item in _items(kubernetes, key)
            if item.get("namespace")
        }
    )
    label_sources = [
        item
        for key in (
            "pods",
            "workloads",
            "nodes",
            "services",
            "ingresses",
            "events",
            "endpoints",
            "resourcequotas",
            "custom_resources",
        )
        for item in _items(kubernetes, key)
    ]
    collection_scopes = _items(kubernetes, "collection_scopes")
    has_observed_inventory_collections = any(
        isinstance(kubernetes.get(key), list) for key in SCOPED_INVENTORY_COLLECTIONS
    )
    has_scoped_inventory_observation = (
        bool(collection_scopes) and has_observed_inventory_collections
    )
    has_legacy_live_inventory_resources = (
        not collection_scopes
        and any(_items(kubernetes, key) for key in LEGACY_LIVE_INVENTORY_COLLECTIONS)
    )
    live_inventory = (
        (has_scoped_inventory_observation or has_legacy_live_inventory_resources)
        and all(not _text(scope.get("label_selector")) for scope in collection_scopes)
    )
    event_capture = _kubernetes_event_capture(kubernetes)
    summary: JsonObject = {
        "namespaces": namespaces,
        "nodes": [
            {
                "name": _text(node.get("name"), "node"),
                "ready": bool(node.get("ready")),
                "pod_count": _scheduled_pod_count(pods, _text(node.get("name"), "node")),
                "version": _text(_mapping(node.get("node_info")).get("kubeletVersion")),
            }
            for node in nodes
        ],
        "pod_phases": dict(phases),
        "services": len(services),
        "labels_complete": resources_complete
        and all(item.get("labels_complete") is True for item in label_sources),
        "resources_complete": resources_complete,
        # The all-namespace Event capture is intentionally separate from user-scoped
        # resources. Its facts are stored only for the Timeline producer; coverage/gap
        # remains on this snapshot until a dedicated Timeline coverage projection exists.
        "kubernetes_event_capture": event_capture,
        "kubernetes_event_facts": _kubernetes_event_facts(kubernetes, event_capture),
        # RCA test/label-selector snapshots are evidence, not authoritative fleet liveness.
        # Legacy no-scope payloads remain live only when they carry standard inventory rows;
        # auxiliary discovery/dynamic-resource-only cuts must not replace the fleet view.
        "live_inventory": live_inventory,
    }
    collection_limits = _mapping(kubernetes.get("collection_limits"))
    if collection_limits:
        summary["collection_limits"] = collection_limits
    collection_coverage = kubernetes_collection_coverage(kubernetes)
    if collection_coverage:
        summary[COLLECTION_COVERAGE_SUMMARY_KEY] = collection_coverage
    detected_provider = normalized_detected_provider(kubernetes.get("detected_provider"))
    if detected_provider is not None:
        summary["detected_provider"] = detected_provider
    api_resource_discovery = _mapping(kubernetes.get("api_resource_discovery"))
    if api_resource_discovery:
        summary["api_resource_discovery"] = api_resource_discovery
    resource_access = _mapping(kubernetes.get("resource_access"))
    if resource_access:
        summary["resource_access"] = resource_access
    dynamic_collections = _items(kubernetes, "dynamic_resource_collections")
    if dynamic_collections:
        summary["dynamic_resource_collections"] = dynamic_collections
    return summary


def _scheduled_pod_count(pods: list[JsonObject], node_name: str) -> int:
    return sum(
        1
        for pod in pods
        if _text(pod.get("node_name")) == node_name
        and _text(pod.get("phase")) not in {"Succeeded", "Failed"}
    )


def _resources_complete(_kubernetes: JsonObject) -> bool:
    # This translator consumes evidence-job results. KubernetesSnapshotQuery is scoped to
    # one namespace and may also carry a label selector, so provider success proves query
    # success rather than full-cluster coverage. A dedicated authoritative sweep contract
    # must be introduced before this path may destructively replace cluster inventory.
    return False


def _kubernetes_event_capture(kubernetes: JsonObject) -> JsonObject:
    source_capture = _mapping(kubernetes.get("event_capture"))
    if source_capture:
        freshness = _mapping(source_capture.get("freshness"))
        coverage = _mapping(source_capture.get("coverage"))
        return {
            "complete": source_capture.get("complete") is True,
            "truncated": source_capture.get("truncated") is True,
            "reason": _text(source_capture.get("reason"), "invalid_capture_contract"),
            "freshness": {
                "observed_at": freshness.get("observed_at"),
                "max_age_seconds": freshness.get("max_age_seconds"),
            },
            "coverage": {
                key: coverage[key]
                for key in (
                    "scope",
                    "pagination",
                    "page_count",
                    "event_count",
                    "resource_version",
                    "gap",
                )
                if key in coverage
            },
        }
    collection_limits = _mapping(kubernetes.get("collection_limits"))
    limits = _mapping(collection_limits.get("lists"))
    event_limit = _mapping(limits.get("events"))
    return {
        "complete": False,
        "truncated": event_limit.get("truncated") is True,
        "reason": "not_requested",
        "freshness": {},
        "coverage": {
            "scope": "all_namespaces",
            "pagination": "continue",
            "gap": "not_requested",
        },
    }


def _kubernetes_event_facts(kubernetes: JsonObject, capture: JsonObject) -> list[JsonObject]:
    """Persist only a complete global Event fact list; partial lists never cross this boundary."""
    if (
        capture.get("complete") is not True
        or capture.get("truncated") is True
        or capture.get("reason") != "complete"
    ):
        return []
    source_capture = _mapping(kubernetes.get("event_capture"))
    source_facts = source_capture.get("events")
    if not isinstance(source_facts, list):
        return []
    facts: list[JsonObject] = []
    for fact in source_facts:
        if not isinstance(fact, dict):
            return []
        safe_fact = {
            key: fact[key]
            for key in (
                "uid",
                "api_version",
                "namespace",
                "name",
                "type",
                "count",
                "last_occurrence_at",
            )
            if key in fact
        }
        if not safe_fact.get("uid") or not safe_fact.get("name"):
            return []
        facts.append(safe_fact)
    return facts
