from __future__ import annotations

import copy
import re

from packages.config.constants import Target
from packages.config.security import RCA_TEST_TARGET_ENVIRONMENTS
from packages.contracts.cost.observations import (
    COST_NAMESPACE_HOURLY_METRIC,
    COST_NAMESPACE_STORAGE_METRIC,
    COST_POD_CPU_HOURLY_METRIC,
    COST_POD_CPU_USE_METRIC,
    COST_POD_MEMORY_HOURLY_METRIC,
    COST_POD_MEMORY_USE_METRIC,
)
from packages.contracts.evidence_policy import (
    TEMPO_RECENT_TRACE_QUERY_NAME,
    TEMPO_RECENT_TRACE_RANGE_SECONDS,
    EvidencePolicyQuery,
    EvidenceProfile,
    EvidenceQueryProvenance,
    EvidenceQueryScope,
    EvidenceQuerySource,
)
from packages.contracts.gateway.requests import (
    AgentPolicy,
    BootstrapPolicy,
    DesiredStatePolicy,
    EvidenceProviderPolicy,
    EvidenceRuntimePolicy,
)
from packages.contracts.target import (
    KUBERNETES_ALL_NAMESPACES_QUERY,
    KUBERNETES_QUERY_SCOPE_CLUSTER_ACCESS,
    KUBERNETES_QUERY_SCOPE_CLUSTER_DISCOVERY,
    KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS,
)
from packages.contracts.traffic.observations import (
    TRAFFIC_CARETTA_FLOW_METRIC,
    TRAFFIC_HUBBLE_FLOW_METRIC,
    TRAFFIC_ISTIO_FLOW_METRIC,
)

DEFAULT_EVIDENCE_FAILURE_POLICY = "allow_partial"
DEFAULT_EVIDENCE_PROVIDER_WORKERS = 1
DEFAULT_EVIDENCE_PROVIDER_MAX_WORKERS = 2
DEFAULT_CLUSTER_ROLE = "target"
MANAGEMENT_CLUSTER_ROLE = "management"
DEFAULT_BOOTSTRAP_MODE = "target"
STANDARD_EVIDENCE_PROFILE: EvidenceProfile = "standard"
DEMO_EVIDENCE_PROFILE: EvidenceProfile = "demo"
MANAGEMENT_EVIDENCE_PROFILE: EvidenceProfile = "management"
EVIDENCE_PROVIDER_KEYS = ("kubernetes", "metrics", "logs", "traces", "metadata")
STANDARD_SLI_FAILURE_RATIO_METRIC = "opsia_sli_failure_ratio"
STANDARD_SLI_REQUEST_RATE_METRIC = "opsia_sli_request_rate"
CONTINUITY_ACTIVE_SESSIONS_METRIC = "opsia_continuity_active_sessions"
STANDARD_SLI_REQUIRED_MATCHERS = (
    'namespace!=""',
    'resource_kind!=""',
    'resource_name!=""',
)

COST_NAMESPACE_HOURLY_QUERY = """sum by (namespace) (
  label_replace(avg_over_time(container_cpu_allocation{namespace!=""}[1h]), "namespace", "$1", "exported_namespace", "(.+)")
  * on(node) group_left() max by (node) (node_cpu_hourly_cost)
) + sum by (namespace) (
  label_replace(avg_over_time(container_memory_allocation_bytes{namespace!=""}[1h]), "namespace", "$1", "exported_namespace", "(.+)")
  / 1073741824 * on(node) group_left() max by (node) (node_ram_hourly_cost)
)"""
COST_NAMESPACE_STORAGE_QUERY = """sum by (namespace) (
  max by (persistentvolume) (pv_hourly_cost)
  * on(persistentvolume) group_left(namespace)
  max by (persistentvolume, namespace) (
    label_replace(kube_persistentvolume_claim_ref{claim_namespace!=""}, "namespace", "$1", "claim_namespace", "(.+)")
  )
)"""
COST_POD_CPU_HOURLY_QUERY = """sum by (namespace, pod) (
  avg_over_time(container_cpu_allocation{namespace!="",pod!=""}[1h])
  * on(node) group_left() max by (node) (node_cpu_hourly_cost)
)"""
COST_POD_MEMORY_HOURLY_QUERY = """sum by (namespace, pod) (
  avg_over_time(container_memory_allocation_bytes{namespace!="",pod!=""}[1h])
  / 1073741824 * on(node) group_left() max by (node) (node_ram_hourly_cost)
)"""
COST_POD_CPU_USE_QUERY = """clamp_max(
  sum by (namespace, pod) (
    rate(container_cpu_usage_seconds_total{namespace!="",pod!="",container!=""}[5m])
  )
  / clamp_min(sum by (namespace, pod) (
    container_cpu_allocation{namespace!="",pod!=""}
  ), 0.001),
  1
)"""
COST_POD_MEMORY_USE_QUERY = """clamp_max(
  sum by (namespace, pod) (
    avg_over_time(container_memory_working_set_bytes{namespace!="",pod!="",container!=""}[5m])
  )
  / clamp_min(sum by (namespace, pod) (
    avg_over_time(container_memory_allocation_bytes{namespace!="",pod!=""}[5m])
  ), 1),
  1
)"""


def _provenance(
    *,
    cluster_id: str,
    evidence_profile: EvidenceProfile,
    query_scope: EvidenceQueryScope,
    namespaces: tuple[str, ...] = (),
    required_matchers: tuple[str, ...] = (),
) -> EvidenceQueryProvenance:
    return EvidenceQueryProvenance(
        cluster_id=cluster_id,
        evidence_profile=evidence_profile,
        backend_scope="cluster_local",
        query_scope=query_scope,
        namespaces=namespaces,
        required_matchers=required_matchers,
    )


def _query(
    *,
    source: EvidenceQuerySource,
    name: str,
    description: str,
    query: str,
    provenance: EvidenceQueryProvenance,
    collection_scope: str | None = None,
    range_seconds: int | None = None,
    step_seconds: int | None = None,
) -> dict[str, object]:
    return EvidencePolicyQuery(
        source=source,
        name=name,
        description=description,
        query=query,
        provenance=provenance,
        collection_scope=collection_scope,
        range_seconds=range_seconds,
        step_seconds=step_seconds,
    ).model_dump(mode="json", exclude_none=True)


def _namespace_query(
    *,
    source: EvidenceQuerySource,
    name: str,
    description: str,
    query: str,
    namespace: str,
    cluster_id: str,
    evidence_profile: EvidenceProfile,
    matcher: str | None = None,
) -> dict[str, object]:
    required_matcher = matcher or namespace
    return _query(
        source=source,
        name=name,
        description=description,
        query=query,
        provenance=_provenance(
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
            query_scope="namespace",
            namespaces=(namespace,),
            required_matchers=(required_matcher,),
        ),
    )


def _cluster_kubernetes_queries(
    cluster_id: str,
    evidence_profile: EvidenceProfile,
) -> list[dict[str, object]]:
    provenance = _provenance(
        cluster_id=cluster_id,
        evidence_profile=evidence_profile,
        query_scope="cluster",
    )
    return [
        _query(
            source="kubernetes",
            name="cluster_wide_event_capture",
            description="Paginated all-namespace Kubernetes Event capture with coverage proof.",
            query=KUBERNETES_ALL_NAMESPACES_QUERY,
            provenance=provenance,
            collection_scope=KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS,
        ),
        _query(
            source="kubernetes",
            name="cluster_api_discovery",
            description="Discover authorized Kubernetes API resources and CRD identities.",
            query=KUBERNETES_ALL_NAMESPACES_QUERY,
            provenance=provenance,
            collection_scope=KUBERNETES_QUERY_SCOPE_CLUSTER_DISCOVERY,
        ),
        _query(
            source="kubernetes",
            name="cluster_access_snapshot",
            description="Collect complete bounded Kubernetes RBAC reverse-lookup evidence.",
            query=KUBERNETES_ALL_NAMESPACES_QUERY,
            provenance=provenance,
            collection_scope=KUBERNETES_QUERY_SCOPE_CLUSTER_ACCESS,
        ),
    ]


def control_namespace_tuple(raw: str | None) -> tuple[str, ...]:
    """등록 settings 의 control_namespaces 문자열을 정규화된 튜플로 만든다."""
    parts = [item.strip() for item in str(raw or "").replace(",", " ").split() if item.strip()]
    deduped: list[str] = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    return tuple(deduped)


def _control_namespace_queries(
    control_namespaces: tuple[str, ...],
    covered: set[str],
    *,
    cluster_id: str,
    evidence_profile: EvidenceProfile,
) -> list[dict[str, object]]:
    """control_namespaces 각각에 대한 네임스페이스 스냅샷 쿼리를 컴파일한다.

    등록 설정의 관리 네임스페이스가 에이전트 수집 범위에 실제로 반영되게 한다 —
    종전에는 standard 프로파일이 에이전트 자신의 네임스페이스(target)만 수집해,
    control 네임스페이스에 배포된 워크로드가 화면(인벤토리)에 보이지 않았다.
    """
    queries: list[dict[str, object]] = []
    used_names: set[str] = set()
    for namespace in control_namespaces:
        if namespace in covered:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", namespace.lower()).strip("_") or "namespace"
        name = f"{slug}_namespace_snapshot"
        if name in used_names:
            continue
        used_names.add(name)
        covered.add(namespace)
        queries.append(
            _namespace_query(
                source="kubernetes",
                name=name,
                description=f"Kubernetes snapshot in the {namespace} control namespace.",
                query=namespace,
                namespace=namespace,
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            )
        )
    return queries


def _control_namespace_log_queries(
    control_namespaces: tuple[str, ...],
    covered: set[str],
    *,
    cluster_id: str,
    evidence_profile: EvidenceProfile,
) -> list[dict[str, object]]:
    """Collect RCA-relevant failures from every configured control namespace."""

    queries: list[dict[str, object]] = []
    used_names: set[str] = set()
    for namespace in control_namespaces:
        if namespace in covered:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", namespace.lower()).strip("_") or "namespace"
        name = f"{slug}_namespace_related_logs"
        if name in used_names:
            continue
        used_names.add(name)
        covered.add(namespace)
        common = {
            "namespace": namespace,
            "matcher": f'k8s_namespace_name="{namespace}"',
            "cluster_id": cluster_id,
            "evidence_profile": evidence_profile,
        }
        queries.extend(
            [
                _namespace_query(
                    source="loki",
                    name=name,
                    description=f"Recent RCA-related logs in the {namespace} control namespace.",
                    query=f'{{k8s_namespace_name="{namespace}"}}',
                    **common,
                ),
                _namespace_query(
                    source="loki",
                    name=f"{slug}_namespace_structured_rejections",
                    description=(
                        f"Recent structured request rejections in the {namespace} "
                        "control namespace."
                    ),
                    query=(
                        f'{{k8s_namespace_name="{namespace}"}} '
                        '| json | outcome="rejected"'
                    ),
                    **common,
                ),
            ]
        )
    return queries


def _metadata_queries(
    control_namespaces: tuple[str, ...],
    *,
    cluster_id: str,
    evidence_profile: EvidenceProfile,
) -> list[dict[str, object]]:
    """Collect change context from the agent namespace and every controlled namespace."""

    queries = [
        _namespace_query(
            source="metadata",
            name="change_context",
            description="Change context metadata in the target agent namespace for RCA.",
            query="target",
            namespace="target",
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
        )
    ]
    covered = {"target"}
    used_names = {"change_context"}
    for namespace in control_namespaces:
        namespace = namespace.strip()
        if not namespace or namespace in covered:
            continue
        covered.add(namespace)
        slug = re.sub(r"[^a-z0-9]+", "_", namespace.lower()).strip("_") or "namespace"
        base_name = f"{slug}_change_context"
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)
        queries.append(
            _namespace_query(
                source="metadata",
                name=name,
                description=f"Change context metadata in the {namespace} control namespace.",
                query=namespace,
                namespace=namespace,
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            )
        )
    return queries


def evidence_provider_queries(
    provider_key: str,
    *,
    cluster_id: str,
    evidence_profile: EvidenceProfile = STANDARD_EVIDENCE_PROFILE,
    control_namespaces: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Compile one server-owned provider query set for an exact cluster profile."""

    if evidence_profile == MANAGEMENT_EVIDENCE_PROFILE:
        if provider_key != "kubernetes":
            return []
        cluster_queries = [
            query
            for query in _cluster_kubernetes_queries(cluster_id, evidence_profile)
            if query.get("collection_scope") != KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS
        ]
        return [
            _namespace_query(
                source="kubernetes",
                name="management_namespace_snapshot",
                description="Kubernetes snapshot in the management namespace.",
                query="management",
                namespace="management",
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            ),
            *cluster_queries,
        ]

    if provider_key == "kubernetes":
        queries = [
            _namespace_query(
                source="kubernetes",
                name="target_namespace_snapshot",
                description="Kubernetes snapshot in the target agent namespace.",
                query="target",
                namespace="target",
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            ),
            *_cluster_kubernetes_queries(cluster_id, evidence_profile),
        ]
        if evidence_profile == DEMO_EVIDENCE_PROFILE:
            queries[1:1] = [
                _namespace_query(
                    source="kubernetes",
                    name="sandbox_namespace_snapshot",
                    description="Kubernetes snapshot in the sandbox demo namespace.",
                    query="sandbox",
                    namespace="sandbox",
                    cluster_id=cluster_id,
                    evidence_profile=evidence_profile,
                ),
                _namespace_query(
                    source="kubernetes",
                    name="color_turf_namespace_snapshot",
                    description="Kubernetes snapshot in the color-turf demo namespace.",
                    query="color-turf",
                    namespace="color-turf",
                    cluster_id=cluster_id,
                    evidence_profile=evidence_profile,
                ),
            ]
        # 등록 설정의 control_namespaces 도 수집 범위에 포함(중복 네임스페이스는 생략).
        covered = (
            {"target", "sandbox", "color-turf"}
            if evidence_profile == DEMO_EVIDENCE_PROFILE
            else {"target"}
        )
        queries[1:1] = _control_namespace_queries(
            control_namespaces,
            covered,
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
        )
        return queries

    if provider_key == "metrics":
        cost_provenance = _provenance(
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
            query_scope="cluster",
            required_matchers=('namespace!=""',),
        )
        cluster_provenance = _provenance(
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
            query_scope="cluster",
        )
        queries = [
            _query(
                source="prometheus",
                name=STANDARD_SLI_FAILURE_RATIO_METRIC,
                description=(
                    "Standard application admission/error SLI ratio scoped by exact "
                    "Kubernetes workload identity."
                ),
                query=(
                    'opsia_sli_failure_ratio{namespace!="",resource_kind!="",resource_name!=""}'
                ),
                provenance=_provenance(
                    cluster_id=cluster_id,
                    evidence_profile=evidence_profile,
                    query_scope="cluster",
                    required_matchers=STANDARD_SLI_REQUIRED_MATCHERS,
                ),
            ),
            _query(
                source="prometheus",
                name=STANDARD_SLI_REQUEST_RATE_METRIC,
                description=(
                    "Standard application request throughput over one minute, retained "
                    "with the same workload/SLI labels as the failure ratio."
                ),
                query=(
                    "sum by (namespace, resource_kind, resource_name, service, sli, symptom) ("
                    'rate(opsia_sli_requests_total{namespace!="",resource_kind!="",'
                    'resource_name!=""}[1m]))'
                ),
                provenance=_provenance(
                    cluster_id=cluster_id,
                    evidence_profile=evidence_profile,
                    query_scope="cluster",
                    required_matchers=STANDARD_SLI_REQUIRED_MATCHERS,
                ),
            ),
            _query(
                source="prometheus",
                name=CONTINUITY_ACTIVE_SESSIONS_METRIC,
                description=(
                    "Exact protected workload active-session continuity gauge. Raw "
                    "series are retained so duplicate identities fail closed."
                ),
                query=(
                    'opsia_continuity_active_sessions{namespace!="",'
                    'resource_kind!="",resource_name!="",continuity_id!="",pod_uid!=""}'
                ),
                provenance=_provenance(
                    cluster_id=cluster_id,
                    evidence_profile=evidence_profile,
                    query_scope="cluster",
                    required_matchers=(
                        *STANDARD_SLI_REQUIRED_MATCHERS,
                        'continuity_id!=""',
                        'pod_uid!=""',
                    ),
                ),
            ),
            _namespace_query(
                source="prometheus",
                name="target_pod_info",
                description="Pods reported by kube-state-metrics in the agent namespace.",
                query='kube_pod_info{namespace="target"}',
                namespace="target",
                matcher='namespace="target"',
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            ),
            _namespace_query(
                source="prometheus",
                name="target_deployment_replicas",
                description="Deployment replicas in the target agent namespace.",
                query='kube_deployment_status_replicas{namespace="target"}',
                namespace="target",
                matcher='namespace="target"',
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            ),
            _query(
                source="prometheus",
                name=COST_NAMESPACE_HOURLY_METRIC,
                description="Namespace CPU and memory allocation hourly rate from OpenCost metrics.",
                query=COST_NAMESPACE_HOURLY_QUERY,
                provenance=cost_provenance,
                collection_scope="cluster_cost_observation",
            ),
            _query(
                source="prometheus",
                name=COST_NAMESPACE_STORAGE_METRIC,
                description="Namespace persistent-volume hourly rate from OpenCost metrics.",
                query=COST_NAMESPACE_STORAGE_QUERY,
                provenance=_provenance(
                    cluster_id=cluster_id,
                    evidence_profile=evidence_profile,
                    query_scope="cluster",
                    required_matchers=('claim_namespace!=""',),
                ),
                collection_scope="cluster_cost_observation",
            ),
            _query(
                source="prometheus",
                name=TRAFFIC_CARETTA_FLOW_METRIC,
                description=(
                    "Caretta flow links collected by the outbound cluster Agent from its "
                    "configured in-cluster Prometheus provider."
                ),
                query=(
                    "max by (client_name, client_namespace, client_kind, server_name, "
                    "server_namespace, server_kind, server_port) (caretta_links_observed)"
                ),
                provenance=cluster_provenance,
            ),
            _query(
                source="prometheus",
                name=TRAFFIC_HUBBLE_FLOW_METRIC,
                description=(
                    "Hubble exported flow counts collected by the outbound cluster Agent "
                    "without a management-plane relay connection."
                ),
                query=(
                    "sum by (source, source_namespace, destination, "
                    "destination_namespace, protocol, verdict) "
                    "(increase(hubble_flows_processed_total[5m]))"
                ),
                provenance=cluster_provenance,
            ),
            _query(
                source="prometheus",
                name=TRAFFIC_ISTIO_FLOW_METRIC,
                description=(
                    "Istio request flows collected by the outbound cluster Agent from its "
                    "configured in-cluster Prometheus provider."
                ),
                query=(
                    "sum by (source_workload, source_workload_namespace, "
                    "destination_workload, destination_workload_namespace, "
                    "destination_service_name, request_protocol, response_code) "
                    '(increase(istio_requests_total{reporter="destination"}[5m]))'
                ),
                provenance=cluster_provenance,
            ),
            _query(
                source="prometheus",
                name=COST_POD_CPU_HOURLY_METRIC,
                description="Pod CPU allocation hourly rate from OpenCost metrics.",
                query=COST_POD_CPU_HOURLY_QUERY,
                provenance=cost_provenance,
                collection_scope="cluster_cost_observation",
            ),
            _query(
                source="prometheus",
                name=COST_POD_MEMORY_HOURLY_METRIC,
                description="Pod memory allocation hourly rate from OpenCost metrics.",
                query=COST_POD_MEMORY_HOURLY_QUERY,
                provenance=cost_provenance,
                collection_scope="cluster_cost_observation",
            ),
            _query(
                source="prometheus",
                name=COST_POD_CPU_USE_METRIC,
                description="Pod CPU use as a bounded fraction of observed allocation.",
                query=COST_POD_CPU_USE_QUERY,
                provenance=cost_provenance,
                collection_scope="cluster_cost_observation",
            ),
            _query(
                source="prometheus",
                name=COST_POD_MEMORY_USE_METRIC,
                description="Pod memory use as a bounded fraction of observed allocation.",
                query=COST_POD_MEMORY_USE_QUERY,
                provenance=cost_provenance,
                collection_scope="cluster_cost_observation",
            ),
        ]
        if evidence_profile == DEMO_EVIDENCE_PROFILE:
            queries.extend(
                [
                    _namespace_query(
                        source="prometheus",
                        name="color_turf_pod_restarts",
                        description="Container restarts in the color-turf demo namespace.",
                        query=('kube_pod_container_status_restarts_total{namespace="color-turf"}'),
                        namespace="color-turf",
                        matcher='namespace="color-turf"',
                        cluster_id=cluster_id,
                        evidence_profile=evidence_profile,
                    ),
                    _namespace_query(
                        source="prometheus",
                        name="color_turf_oom_terminated",
                        description="OOMKilled containers in the color-turf demo namespace.",
                        query=(
                            "kube_pod_container_status_last_terminated_reason"
                            '{namespace="color-turf",reason="OOMKilled"}'
                        ),
                        namespace="color-turf",
                        matcher='namespace="color-turf"',
                        cluster_id=cluster_id,
                        evidence_profile=evidence_profile,
                    ),
                ]
            )
        return queries

    if provider_key == "logs":
        queries = [
            _namespace_query(
                source="loki",
                name="target_namespace_errors",
                description="Error logs in the target agent namespace.",
                query='{k8s_namespace_name="target"} |= "ERROR"',
                namespace="target",
                matcher='k8s_namespace_name="target"',
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            ),
            _namespace_query(
                source="loki",
                name="node_collector_runtime_samples",
                description="Structured samples emitted by optional-node-collector.",
                query=(
                    '{k8s_namespace_name="target", k8s_container_name="node-collector"} '
                    '|= "node_runtime_sample"'
                ),
                namespace="target",
                matcher='k8s_namespace_name="target"',
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            ),
            _namespace_query(
                source="loki",
                name="target_agent_warnings",
                description="Warnings or failures emitted by the target cluster agent.",
                query=(
                    '{k8s_namespace_name="target", k8s_container_name="cluster-agent"} '
                    '|~ "WARN|ERROR|failed"'
                ),
                namespace="target",
                matcher='k8s_namespace_name="target"',
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            ),
        ]
        if evidence_profile == DEMO_EVIDENCE_PROFILE:
            queries.extend(
                [
                    _namespace_query(
                        source="loki",
                        name="sandbox_namespace_errors",
                        description="Error/fatal logs in the sandbox demo namespace.",
                        query=('{k8s_namespace_name="sandbox"} |~ "ERROR|FATAL|panic"'),
                        namespace="sandbox",
                        matcher='k8s_namespace_name="sandbox"',
                        cluster_id=cluster_id,
                        evidence_profile=evidence_profile,
                    ),
                    _namespace_query(
                        source="loki",
                        name="color_turf_runtime_failures",
                        description="Runtime failures in the color-turf demo namespace.",
                        query=(
                            '{k8s_namespace_name="color-turf"} '
                            '|~ "OOM|out of memory|chaos.oom|ERROR|FATAL|panic"'
                        ),
                        namespace="color-turf",
                        matcher='k8s_namespace_name="color-turf"',
                        cluster_id=cluster_id,
                        evidence_profile=evidence_profile,
                    ),
                ]
            )
        # Demo presets may add optimized queries, but configured control
        # namespaces still need one unfiltered bounded stream. RCA applies the
        # incident namespace/Pod/structured identity after collection, so the
        # policy never encodes an application name, event name, or conclusion.
        covered = {"target"}
        queries.extend(
            _control_namespace_log_queries(
                control_namespaces,
                covered,
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
            )
        )
        return queries

    if provider_key == "traces":
        # Each target uses its own in-cluster Tempo service, so an unscoped
        # TraceQL selector cannot cross cluster boundaries.
        return [
            _query(
                source="tempo",
                name=TEMPO_RECENT_TRACE_QUERY_NAME,
                description="Recent traces from the target cluster-local Tempo backend.",
                query="{}",
                range_seconds=TEMPO_RECENT_TRACE_RANGE_SECONDS,
                provenance=_provenance(
                    cluster_id=cluster_id,
                    evidence_profile=evidence_profile,
                    query_scope="cluster",
                ),
            )
        ]
    if provider_key == "metadata":
        return _metadata_queries(
            control_namespaces,
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
        )
    return []


def profile_default_query_names() -> frozenset[str]:
    """Return every reserved server preset name across target profiles."""

    names: set[str] = set()
    for profile in (STANDARD_EVIDENCE_PROFILE, DEMO_EVIDENCE_PROFILE):
        for provider_key in EVIDENCE_PROVIDER_KEYS:
            names.update(
                str(query["name"])
                for query in evidence_provider_queries(
                    provider_key,
                    cluster_id=Target.DEFAULT_CLUSTER_ID,
                    evidence_profile=profile,
                )
            )
    return frozenset(names)


def preserve_server_owned_evidence_queries(
    policy: AgentPolicy,
    *,
    cluster_id: str,
    evidence_profile: EvidenceProfile,
    control_namespaces: tuple[str, ...] = (),
) -> AgentPolicy:
    """Rebase canonical collection queries while retaining operator extensions.

    Evidence query presets define the management plane's minimum observation
    contract. A partial policy update may tune workers, intervals, or disable a
    provider, but it must not erase that contract: doing so leaves a connected
    agent reporting empty inventory. Queries with non-reserved names remain
    operator-owned and survive the rebase.
    """

    payload = policy.model_dump(mode="python")
    evidence = payload["evidence"]
    providers = evidence["providers"]
    evidence["profile"] = evidence_profile
    reserved_names = profile_default_query_names()

    for provider_key in EVIDENCE_PROVIDER_KEYS:
        defaults = evidence_provider_queries(
            provider_key,
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
            control_namespaces=control_namespaces,
        )
        provider = providers.get(provider_key)
        if provider is None:
            providers[provider_key] = default_evidence_provider_policy(
                provider_key,
                int(Target.DEFAULT_EVIDENCE_INTERVAL_SECONDS),
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
                control_namespaces=control_namespaces,
            ).model_dump(mode="python")
            continue

        defaults_by_name = {str(query["name"]): copy.deepcopy(query) for query in defaults}
        seen_defaults: set[str] = set()
        rebased: list[dict[str, object]] = []
        for raw_query in provider.get("queries", []):
            query = dict(raw_query) if isinstance(raw_query, dict) else {}
            name = query.get("name")
            if isinstance(name, str) and name in defaults_by_name:
                if name not in seen_defaults:
                    rebased.append(copy.deepcopy(defaults_by_name[name]))
                    seen_defaults.add(name)
                continue
            if isinstance(name, str) and name in reserved_names:
                continue
            rebased.append(copy.deepcopy(query))

        for default_query in defaults:
            name = str(default_query["name"])
            if name not in seen_defaults:
                rebased.append(copy.deepcopy(default_query))
        provider["queries"] = rebased

    return AgentPolicy.model_validate(payload)


def evidence_profile_for_registration(
    *,
    cluster_role: str,
    environment: str,
    install_sample_workload: bool,
) -> EvidenceProfile:
    if cluster_role == MANAGEMENT_CLUSTER_ROLE:
        return MANAGEMENT_EVIDENCE_PROFILE
    if install_sample_workload or environment.strip().casefold() in RCA_TEST_TARGET_ENVIRONMENTS:
        return DEMO_EVIDENCE_PROFILE
    return STANDARD_EVIDENCE_PROFILE


def default_evidence_provider_policy(
    provider_key: str,
    interval_seconds: int,
    *,
    cluster_id: str = Target.DEFAULT_CLUSTER_ID,
    evidence_profile: EvidenceProfile = STANDARD_EVIDENCE_PROFILE,
    control_namespaces: tuple[str, ...] = (),
    enabled: bool | None = None,
    queries: list[dict[str, object]] | None = None,
) -> EvidenceProviderPolicy:
    """Build the default policy for one evidence provider."""
    compiled_queries = (
        evidence_provider_queries(
            provider_key,
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
            control_namespaces=control_namespaces,
        )
        if queries is None
        else list(queries)
    )
    return EvidenceProviderPolicy(
        enabled=bool(compiled_queries) if enabled is None else enabled,
        interval_seconds=interval_seconds,
        min_workers=DEFAULT_EVIDENCE_PROVIDER_WORKERS,
        max_workers=DEFAULT_EVIDENCE_PROVIDER_MAX_WORKERS,
        queries=compiled_queries,
    )


def default_evidence_providers(
    interval_seconds: int,
    *,
    cluster_id: str = Target.DEFAULT_CLUSTER_ID,
    cluster_role: str = DEFAULT_CLUSTER_ROLE,
    evidence_profile: EvidenceProfile | None = None,
    control_namespaces: tuple[str, ...] = (),
) -> dict[str, EvidenceProviderPolicy]:
    """Build default provider policies for all known providers."""
    resolved_profile = evidence_profile or (
        MANAGEMENT_EVIDENCE_PROFILE
        if cluster_role == MANAGEMENT_CLUSTER_ROLE
        else STANDARD_EVIDENCE_PROFILE
    )
    if cluster_role == MANAGEMENT_CLUSTER_ROLE and resolved_profile != MANAGEMENT_EVIDENCE_PROFILE:
        raise ValueError("management clusters require the management evidence profile")
    if cluster_role != MANAGEMENT_CLUSTER_ROLE and resolved_profile == MANAGEMENT_EVIDENCE_PROFILE:
        raise ValueError("target clusters cannot use the management evidence profile")
    return {
        provider_key: default_evidence_provider_policy(
            provider_key,
            interval_seconds,
            cluster_id=cluster_id,
            evidence_profile=resolved_profile,
            control_namespaces=control_namespaces,
        )
        for provider_key in EVIDENCE_PROVIDER_KEYS
    }


def default_agent_policy(
    *,
    cluster_id: str,
    cluster_role: str = DEFAULT_CLUSTER_ROLE,
    interval_seconds: int = int(Target.DEFAULT_EVIDENCE_INTERVAL_SECONDS),
    failure_policy: str = DEFAULT_EVIDENCE_FAILURE_POLICY,
    bootstrap_mode: str = DEFAULT_BOOTSTRAP_MODE,
    generation: int = 1,
    evidence_profile: EvidenceProfile | None = None,
    control_namespaces: tuple[str, ...] = (),
) -> AgentPolicy:
    """Build the default policy used by a target cluster agent."""
    resolved_profile = evidence_profile or (
        MANAGEMENT_EVIDENCE_PROFILE
        if cluster_role == MANAGEMENT_CLUSTER_ROLE
        else STANDARD_EVIDENCE_PROFILE
    )
    return AgentPolicy(
        cluster_id=cluster_id,
        cluster_role=cluster_role,
        generation=generation,
        evidence=EvidenceRuntimePolicy(
            profile=resolved_profile,
            failure_policy=failure_policy,
            providers=default_evidence_providers(
                interval_seconds,
                cluster_id=cluster_id,
                cluster_role=cluster_role,
                evidence_profile=resolved_profile,
                control_namespaces=control_namespaces,
            ),
        ),
        bootstrap=BootstrapPolicy(mode=bootstrap_mode),
        desired_state=DesiredStatePolicy(),
    )


def enabled_provider_keys(policy: AgentPolicy, requested_provider_keys: list[str]) -> list[str]:
    """Return requested provider keys that are enabled by policy."""
    keys: list[str] = []
    for provider_key in dict.fromkeys(requested_provider_keys):
        provider_policy = policy.evidence.providers.get(provider_key)
        if provider_policy is None or provider_policy.enabled:
            keys.append(provider_key)
    return keys


def provider_policy_snapshots(
    policy: AgentPolicy,
    provider_keys: list[str],
) -> dict[str, dict[str, object]]:
    """Return serializable policy data for each queued provider job."""
    return {
        provider_key: policy.evidence.providers.get(
            provider_key,
            EvidenceProviderPolicy(),
        ).model_dump()
        for provider_key in provider_keys
    }
