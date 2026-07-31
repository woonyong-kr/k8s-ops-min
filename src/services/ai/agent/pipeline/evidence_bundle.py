from __future__ import annotations

from copy import deepcopy

from domains.rca.events import (
    ClusterEvidenceReceivedBody,
    Evidence,
    EvidenceBundle,
    EvidenceItem,
    IncidentRecord,
    MissingEvidenceCheck,
)
from services.ai.agent.causes.engine import required_evidence_sources
from services.ai.agent.pipeline.evidence import EVIDENCE_LINEAGE_KEY, EVIDENCE_SOURCE_SCHEMA_VERSION
from services.ai.agent.pipeline.symptom import derive_symptom, resolve_resource

EvidenceSource = ClusterEvidenceReceivedBody | Evidence
SENSITIVE_CHANGE_TOKENS = (
    "secret",
    "token",
    "password",
    "credential",
    "private",
    "key",
)
REDACTED_CHANGE_VALUE = "redacted"

MAX_KUBERNETES_PODS = 16
MAX_KUBERNETES_EVENTS = 24
MAX_KUBERNETES_NODES = 12
MAX_LOG_ENTRIES = 8
MAX_LOG_STREAMS = 4
MAX_LOG_VALUES = 20
MAX_LOG_TRACE_IDS = 20
MAX_TEXT_LENGTH = 1600
MAX_METRIC_RESULTS = 12
MAX_METRIC_SERIES = 8
MAX_TRACE_RESULTS = 12
COLLECTION_STATUS_METADATA_KEY = "collection_status"
COLLECTION_PROVIDERS_KEY = "providers"
COLLECTION_UNAVAILABLE_STATES = {"unavailable", "not_queried"}
NO_PROVIDER_RESULTS_REASON = "no_provider_results"


def extract_resource(kubernetes: dict) -> tuple[str, str, str | None]:
    # incident 분류(pipeline/incident.py)와 같은 규칙 — 명시 resource > 유도 신호의 리소스.
    return resolve_resource(kubernetes, derive_symptom(kubernetes).signal)


def extract_symptom(kubernetes: dict) -> str:
    # incident 분류와 같은 규칙 — 명시 symptom > snapshot 신호 유도 > "unknown".
    return derive_symptom(kubernetes).symptom


def build_incident_evidence_bundle(
    evt: EvidenceSource,
    incident: IncidentRecord,
) -> EvidenceBundle:
    required_sources = required_evidence_sources(incident)
    items = collect_evidence_items(evt)
    present_sources = {item.source for item in items}
    missing_evidence = [source for source in required_sources if source not in present_sources]
    return EvidenceBundle(
        incident_id=incident.incident_id,
        items=items,
        missing_evidence=missing_evidence,
        complete=not missing_evidence,
        missing_evidence_checks=missing_source_checks(missing_evidence, evt),
    )


def compact_evidence_reference(evidence: Evidence) -> Evidence:
    """Downstream 이벤트에는 원본 중복 대신 object_ref 중심의 얇은 참조만 싣는다."""
    return Evidence(
        cluster_id=evidence.cluster_id,
        kubernetes=compact_reference_payload(evidence.kubernetes),
        metrics=compact_reference_payload(evidence.metrics),
        logs=[],
        traces=compact_reference_payload(evidence.traces),
        object_ref=evidence.object_ref,
        metadata=compact_reference_payload(evidence.metadata),
        workspace_id=evidence.workspace_id,
    )


def compact_reference_payload(payload: dict) -> dict:
    reference: dict = {}
    lineage = payload.get(EVIDENCE_LINEAGE_KEY) if isinstance(payload, dict) else None
    if isinstance(lineage, dict):
        reference[EVIDENCE_LINEAGE_KEY] = dict(lineage)
    cluster = payload.get("cluster") if isinstance(payload, dict) else None
    if isinstance(cluster, dict):
        reference["cluster"] = {
            key: cluster.get(key)
            for key in ("cluster_id", "namespace", "collected_at")
            if cluster.get(key) is not None
        }
    return reference


def evidence_ref_for(evt: EvidenceSource, source: str, name: str) -> str:
    if isinstance(evt, Evidence):
        base_ref = evt.object_ref
    else:
        base_ref = evt.evidence_key or f"cluster:{evt.workspace_id}:{evt.cluster_id}"
    return f"{base_ref}#{source}:{name}"


def source_check_id(source: str, name: str) -> str:
    return f"evidence:{source}:{name}"


def source_query(source: str, name: str) -> str:
    return f"{source}.{name}"


def evidence_item(
    evt: EvidenceSource,
    *,
    source: str,
    name: str,
    value: dict,
    summary: str,
) -> EvidenceItem:
    check_id = source_check_id(source, name)
    query = source_query(source, name)
    return EvidenceItem(
        source=source,
        name=name,
        value=with_item_lineage(
            value,
            item_lineage(evt, source=source, name=name, check_id=check_id, query=query),
        ),
        summary=summary,
        evidence_ref=evidence_ref_for(evt, source, name),
        check_id=check_id,
        query=query,
    )


def item_lineage(
    evt: EvidenceSource,
    *,
    source: str,
    name: str,
    check_id: str,
    query: str,
) -> dict:
    base = lineage_from_source_payload(evt, source)
    lineage = {
        "schema_version": EVIDENCE_SOURCE_SCHEMA_VERSION,
        "source": source,
        "name": name,
        "check_id": check_id,
        "query": query,
        **base,
    }
    return {key: value for key, value in lineage.items() if value not in (None, "", [])}


def with_item_lineage(value: dict, lineage: dict) -> dict:
    data = deepcopy(value)
    current = data.get(EVIDENCE_LINEAGE_KEY)
    if isinstance(current, dict):
        data[EVIDENCE_LINEAGE_KEY] = {**lineage, **current}
    else:
        data[EVIDENCE_LINEAGE_KEY] = lineage
    return data


def lineage_from_source_payload(evt: EvidenceSource, source: str) -> dict:
    payload = source_payload(evt, source)
    if isinstance(payload, dict):
        lineage = payload.get(EVIDENCE_LINEAGE_KEY)
        if isinstance(lineage, dict):
            return enrich_lineage_from_payload(dict(lineage), source, payload)
        return enrich_lineage_from_payload({}, source, payload)
    if source == "logs" and isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict) and isinstance(entry.get(EVIDENCE_LINEAGE_KEY), dict):
                return enrich_lineage_from_logs(dict(entry[EVIDENCE_LINEAGE_KEY]), payload)
        return enrich_lineage_from_logs({}, payload)
    return {}


def source_payload(evt: EvidenceSource, source: str) -> object:
    if source == "kubernetes":
        return evt.kubernetes
    if source == "metrics":
        return evt.metrics
    if source == "logs":
        return evt.logs
    if source == "traces":
        return evt.traces
    if source == "metadata":
        return evt.metadata
    return {}


def enrich_lineage_from_payload(lineage: dict, source: str, payload: dict) -> dict:
    if source == "kubernetes":
        cluster = payload.get("cluster")
        if isinstance(cluster, dict):
            lineage.setdefault("collected_at", cluster.get("collected_at"))
    if source in {"metrics", "traces"}:
        results = payload.get("results")
        if isinstance(results, dict):
            lineage.setdefault("query_names", sorted(str(key) for key in results))
            lineage.setdefault("query_count", len(results))
            lineage.setdefault("source_version", payload.get("source"))
    return lineage


def enrich_lineage_from_logs(lineage: dict, entries: list[dict]) -> dict:
    query_names = sorted(
        {
            str(entry["query_name"])
            for entry in entries
            if isinstance(entry, dict) and entry.get("query_name")
        }
    )
    if query_names:
        lineage.setdefault("query_names", query_names)
        lineage.setdefault("query_count", len(query_names))
    for entry in entries:
        if isinstance(entry, dict) and entry.get("source"):
            lineage.setdefault("source_version", entry.get("source"))
            break
    return lineage


def missing_source_checks(
    missing_evidence: list[str],
    evt: EvidenceSource | None = None,
) -> list[MissingEvidenceCheck]:
    checks: list[MissingEvidenceCheck] = []
    for source in missing_evidence:
        status = provider_collection_status(evt, source) if evt is not None else {}
        state = str(status.get("status") or "")
        raw_reason_codes = status.get("reason_codes")
        reason_codes = [
            value
            for value in raw_reason_codes
            if isinstance(value, str) and value
        ] if isinstance(raw_reason_codes, list) else []
        if state in COLLECTION_UNAVAILABLE_STATES or state == "partial":
            reason_suffix = f" ({', '.join(reason_codes)})" if reason_codes else ""
            check_status = state
            reason = f"{source} evidence collection is {state}{reason_suffix}."
        elif evt is not None and legacy_empty_telemetry_source(evt, source):
            check_status = "unavailable"
            reason = f"{source} evidence collection is unavailable ({NO_PROVIDER_RESULTS_REASON})."
        else:
            check_status = "missing"
            reason = f"{source} evidence query/check must complete before RCA can be finalized."
        checks.append(
            MissingEvidenceCheck(
                check_id=f"evidence:{source}:required",
                source=source,
                status=check_status,
                reason=reason,
            )
        )
    return checks


def evidence_collection_status(evt: EvidenceSource) -> dict:
    if isinstance(evt, ClusterEvidenceReceivedBody):
        return evt.collection_status
    value = evt.metadata.get(COLLECTION_STATUS_METADATA_KEY)
    return value if isinstance(value, dict) else {}


def provider_collection_status(evt: EvidenceSource | None, source: str) -> dict:
    if evt is None:
        return {}
    providers = evidence_collection_status(evt).get(COLLECTION_PROVIDERS_KEY)
    if not isinstance(providers, dict):
        return {}
    value = providers.get(source)
    return value if isinstance(value, dict) else {}


def source_evidence_available(evt: EvidenceSource, source: str) -> bool:
    state = provider_collection_status(evt, source).get("status")
    if state in COLLECTION_UNAVAILABLE_STATES:
        return False
    payload = source_payload(evt, source)
    if source == "logs":
        return isinstance(payload, list) and bool(payload)
    if not isinstance(payload, dict):
        return False
    if source == "metrics":
        alertmanager = payload.get("alertmanager")
        if isinstance(alertmanager, dict) and bool(alertmanager):
            return True
        if payload.get("source") == "prometheus" or "results" in payload:
            results = payload.get("results")
            return isinstance(results, dict) and bool(results)
    if source == "traces" and (
        payload.get("source") == "tempo" or "results" in payload
    ):
        results = payload.get("results")
        return isinstance(results, dict) and bool(results)
    return any(
        value not in (None, "", [], {})
        for key, value in payload.items()
        if key != EVIDENCE_LINEAGE_KEY
    )


def legacy_empty_telemetry_source(evt: EvidenceSource, source: str) -> bool:
    if source not in {"metrics", "traces"}:
        return False
    payload = source_payload(evt, source)
    if not isinstance(payload, dict):
        return False
    expected_source = "prometheus" if source == "metrics" else "tempo"
    if payload.get("source") != expected_source and "results" not in payload:
        return False
    results = payload.get("results")
    return isinstance(results, dict) and not results


# Loki 정규화 payload 의 stream 라벨 중 네임스페이스로 인정하는 키.
LOG_STREAM_NAMESPACE_LABELS = ("k8s_namespace_name", "namespace")
LOG_STREAM_POD_LABELS = ("k8s_pod_name", "pod", "pod_name", "kubernetes_pod_name")
LOG_ENTRY_NAMESPACE_KEYS = ("namespace", "k8s_namespace_name")
LOG_ENTRY_POD_KEYS = ("pod", "k8s_pod_name", "pod_name", "kubernetes_pod_name")


def select_incident_log_entries(
    logs: list[dict],
    namespace: str | None,
    pod_names: set[str] | None = None,
) -> list[dict]:
    """incident 네임스페이스의 로그만 근거로 채택한다(다른 네임스페이스 노이즈 제외).

    에이전트 정책의 로그 쿼리는 네임스페이스별로 여러 개(target/sandbox) 실행되는데,
    RCA 근거·판별 신호에는 incident 리소스가 속한 네임스페이스의 로그만 의미가 있다.
    (예: sandbox 워크로드 장애 리포트에 target 네임스페이스 loki 자체 ERROR 로그가
    섞여 들어가던 문제.) 판정 규칙:
    - Loki 정규화 entry(streams 보유): 네임스페이스 라벨이 incident 와 일치하는 stream 만
      남기고, 남는 stream 이 없으면 entry 자체를 제외한다. 라벨이 없는 stream 은
      귀속 불가라 보수적으로 유지한다.
    - streams 가 없는 entry(단순 {"line": ...} 레거시 데이터/webhook 형태): 귀속 불가 → 유지.
    - incident 네임스페이스를 모르면 필터하지 않는다.

    참고: 여기서 걸러도 원본 `Evidence.logs`(수집 원문)에는 전체 네임스페이스 로그가
    남는다. 리포트가 소비하는 근거 번들(evidence_bundle)만 정제하는 최소 수정이며,
    수집 시점 분리(incident 별 로그 쿼리 실행)는 evidence 수집 파이프라인 후속 과제다.
    """
    if not namespace and pod_names is None:
        return list(logs)
    selected: list[dict] = []
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        streams = entry.get("streams")
        if not isinstance(streams, list):
            if pod_names is None:
                selected.append(entry)
            continue
        kept = [
            stream
            for stream in streams
            if isinstance(stream, dict)
            and stream_matches_incident_scope(stream, namespace, pod_names)
        ]
        if not kept:
            continue
        matched_entries = select_incident_matched_entries(entry, namespace, pod_names)
        selected_entry = {
            **entry,
            "streams": kept,
            "line_count": sum(len(stream.get("values") or []) for stream in kept),
        }
        if matched_entries is not None:
            selected_entry["matched_entries"] = matched_entries
        apply_scoped_log_summaries(selected_entry, kept)
        selected.append(selected_entry)
    return selected


def apply_scoped_log_summaries(entry: dict, streams: list[dict]) -> None:
    """Use selected stream summaries for RCA related log counts."""
    pattern_counts = summed_stream_counts(streams, "pattern_counts", entry.get("pattern_counts"))
    if pattern_counts is not None:
        entry["pattern_counts"] = pattern_counts
    severity_counts = summed_stream_counts(streams, "severity_counts", entry.get("severity_counts"))
    if severity_counts is not None:
        entry["severity_counts"] = severity_counts
    trace_ids = selected_stream_trace_ids(streams)
    if trace_ids is not None:
        entry["trace_ids"] = trace_ids


def summed_stream_counts(
    streams: list[dict],
    key: str,
    base_counts: object,
) -> dict[str, int] | None:
    """Sum one count object from selected log streams."""
    counts: dict[str, int] = {}
    if isinstance(base_counts, dict):
        counts.update(
            {
                str(count_key): 0
                for count_key, count in base_counts.items()
                if isinstance(count, int)
            }
        )
    found = False
    for stream in streams:
        value = stream.get(key)
        if not isinstance(value, dict):
            continue
        found = True
        for count_key, count in value.items():
            if isinstance(count, int):
                counts[str(count_key)] = counts.get(str(count_key), 0) + count
    return counts if found else None


def selected_stream_trace_ids(streams: list[dict]) -> list[str] | None:
    """Return trace IDs from selected log streams, or None for legacy streams."""
    trace_ids: list[str] = []
    seen: set[str] = set()
    found = False
    for stream in streams:
        raw_trace_ids = stream.get("trace_ids")
        if not isinstance(raw_trace_ids, list):
            continue
        found = True
        for value in raw_trace_ids:
            trace_id = str(value)
            if trace_id and trace_id not in seen and len(trace_ids) < MAX_LOG_TRACE_IDS:
                seen.add(trace_id)
                trace_ids.append(trace_id)
    return trace_ids if found else None


def select_incident_matched_entries(
    entry: dict,
    namespace: str | None,
    pod_names: set[str] | None,
) -> list[dict] | None:
    """Keep matched log summaries in the same incident scope as streams."""
    matched_entries = entry.get("matched_entries")
    if not isinstance(matched_entries, list):
        return None
    return [
        matched_entry
        for matched_entry in matched_entries
        if isinstance(matched_entry, dict)
        and matched_entry_matches_incident_scope(matched_entry, namespace, pod_names)
    ]


def matched_entry_matches_incident_scope(
    entry: dict,
    namespace: str | None,
    pod_names: set[str] | None,
) -> bool:
    """Match a structured log summary to the incident namespace and Pod scope."""
    entry_namespace = first_present_string(entry, LOG_ENTRY_NAMESPACE_KEYS)
    if pod_names is None:
        return not namespace or entry_namespace is None or entry_namespace == namespace
    if not namespace or not pod_names:
        return False
    entry_pod = first_present_string(entry, LOG_ENTRY_POD_KEYS)
    return entry_namespace == namespace and entry_pod in pod_names


def first_present_string(payload: dict, keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty string value for one of the given keys."""
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def stream_matches_incident_scope(
    stream: dict,
    namespace: str | None,
    pod_names: set[str] | None,
) -> bool:
    """일반 incident는 기존 규칙, RCA test는 namespace와 Pod를 모두 엄격히 확인한다."""
    if pod_names is None:
        return not namespace or stream_matches_namespace(stream, namespace)
    if not namespace or not pod_names:
        return False
    labels = stream.get("stream")
    if not isinstance(labels, dict):
        return False
    namespace_value = next(
        (str(labels[key]) for key in LOG_STREAM_NAMESPACE_LABELS if labels.get(key) is not None),
        None,
    )
    pod_value = next(
        (str(labels[key]) for key in LOG_STREAM_POD_LABELS if labels.get(key) is not None),
        None,
    )
    return namespace_value == namespace and pod_value in pod_names


def stream_matches_namespace(stream: dict, namespace: str) -> bool:
    labels = stream.get("stream")
    if not isinstance(labels, dict):
        return True  # 라벨 없음 → 귀속 불가, 보수적으로 유지
    for key in LOG_STREAM_NAMESPACE_LABELS:
        value = labels.get(key)
        if value is not None:
            return str(value) == namespace
    return True


def rca_test_pod_names(metadata: dict) -> set[str] | None:
    """RCA test metadata가 있으면 Pod 귀속을 필수화하고, 일반 incident면 None을 반환한다."""
    test_context = metadata.get("rca_test")
    if not isinstance(test_context, dict):
        return None
    raw_names = test_context.get("pod_names")
    if not isinstance(raw_names, list):
        return set()
    return {str(name).strip() for name in raw_names if str(name).strip()}


def change_context_payload(metadata: dict) -> dict:
    raw = metadata.get("change_context")
    if isinstance(raw, dict):
        return dict(raw)
    known_keys = {"recent_changes", "gitops", "image", "rollout", "config", "risk"}
    if any(key in metadata for key in known_keys):
        return dict(metadata)
    return {}


def sanitize_change_context(value: dict) -> dict:
    sanitized = dict(value)
    recent_changes = sanitized.get("recent_changes")
    if isinstance(recent_changes, list):
        sanitized["recent_changes"] = [
            sanitize_recent_change(change) for change in recent_changes if isinstance(change, dict)
        ]
    return sanitized


def sanitize_recent_change(change: dict) -> dict:
    sanitized = dict(change)
    if is_sensitive_change(sanitized):
        for key in ("before", "after"):
            if key in sanitized and sanitized[key] not in (None, ""):
                sanitized[key] = REDACTED_CHANGE_VALUE
    return sanitized


def is_sensitive_change(change: dict) -> bool:
    text = " ".join(
        str(change.get(key) or "") for key in ("change_type", "target_resource", "field", "source")
    ).casefold()
    return any(token in text for token in SENSITIVE_CHANGE_TOKENS)


def has_change_context(value: dict) -> bool:
    for key in ("recent_changes", "gitops", "image", "rollout", "config", "risk"):
        section = value.get(key)
        if isinstance(section, list) and section:
            return True
        if isinstance(section, dict) and section:
            return True
    return False


def current_workload_snapshot_payload(metadata: dict) -> dict:
    raw = metadata.get("current_workload_snapshot")
    if isinstance(raw, dict) and raw:
        return dict(raw)
    change_context = metadata.get("change_context")
    if isinstance(change_context, dict):
        raw = change_context.get("current_workload_snapshot")
        if isinstance(raw, dict) and raw:
            return dict(raw)
    return {}


def current_workload_snapshots_payload(metadata: dict) -> dict:
    raw = metadata.get("current_workload_snapshots")
    if not isinstance(raw, list):
        change_context = metadata.get("change_context")
        if isinstance(change_context, dict):
            raw = change_context.get("current_workload_snapshots")
    if not isinstance(raw, list):
        return {}
    snapshots = [dict(item) for item in raw if isinstance(item, dict) and item]
    if not snapshots:
        return {}
    payload = {"items": snapshots}
    attach_collection_limit(payload, metadata, "current_workload_snapshots")
    return payload


def metadata_list_payload(metadata: dict, key: str) -> dict:
    raw = metadata.get(key)
    if not isinstance(raw, list):
        change_context = metadata.get("change_context")
        if isinstance(change_context, dict):
            raw = change_context.get(key)
    if not isinstance(raw, list):
        return {}
    items = [dict(item) for item in raw if isinstance(item, dict) and item]
    if not items:
        return {}
    payload = {"items": items}
    attach_collection_limit(payload, metadata, key)
    return payload


def attach_collection_limit(payload: dict, metadata: dict, key: str) -> None:
    """Attach truncation metadata for one promoted metadata list."""
    limit = metadata_collection_limit(metadata, key)
    if limit:
        payload["collection_limit"] = limit


def metadata_collection_limit(metadata: dict, key: str) -> dict:
    """Return collection limit details for one metadata list."""
    limits = metadata.get("collection_limits")
    if not isinstance(limits, dict):
        change_context = metadata.get("change_context")
        if isinstance(change_context, dict):
            limits = change_context.get("collection_limits")
    if not isinstance(limits, dict):
        return {}
    lists = limits.get("lists")
    if not isinstance(lists, dict):
        return {}
    limit = lists.get(key)
    return dict(limit) if isinstance(limit, dict) and limit else {}


def collect_change_context(
    evt: EvidenceSource,
    *,
    resource_kind: str,
    resource_name: str,
    namespace: str | None,
) -> dict | None:
    payload = change_context_payload(evt.metadata)
    if not payload:
        return None
    value = sanitize_change_context(payload)
    value.setdefault(
        "resource",
        {
            "namespace": namespace,
            "workload_kind": resource_kind,
            "workload_name": resource_name,
        },
    )
    return value if has_change_context(value) else None


def collect_evidence_items(evt: EvidenceSource) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    resource_kind, resource_name, namespace = extract_resource(evt.kubernetes)
    symptom = extract_symptom(evt.kubernetes)
    target_summary = (
        f"{namespace or 'unknown'} namespace의 {resource_kind} "
        f"{resource_name}에서 {symptom} 증상이 보고되었습니다."
    )

    if source_evidence_available(evt, "kubernetes"):
        items.append(
            evidence_item(
                evt,
                source="kubernetes",
                name="cluster_resource_state",
                value=compact_kubernetes_value(
                    evt.kubernetes,
                    namespace=namespace,
                    resource_kind=resource_kind,
                    resource_name=resource_name,
                ),
                summary=f"{target_summary} Kubernetes 상태 근거입니다.",
            )
        )
    if source_evidence_available(evt, "metrics"):
        items.append(
            evidence_item(
                evt,
                source="metrics",
                name="telemetry_metrics",
                value=compact_metrics_value(evt.metrics),
                summary=f"{target_summary} Metric snapshot 근거입니다.",
            )
        )
    log_entries = (
        select_incident_log_entries(
            evt.logs,
            namespace,
            pod_names=rca_test_pod_names(evt.metadata),
        )
        if source_evidence_available(evt, "logs")
        else []
    )
    if log_entries:
        items.append(
            evidence_item(
                evt,
                source="logs",
                name="related_logs",
                value={"entries": compact_log_entries(log_entries)},
                summary=f"{target_summary} Log tail 근거입니다.",
            )
        )
    if source_evidence_available(evt, "traces"):
        items.append(
            evidence_item(
                evt,
                source="traces",
                name="related_traces",
                value=compact_traces_value(evt.traces),
                summary=f"{target_summary} Trace 근거입니다.",
            )
        )
    workload_snapshots = current_workload_snapshots_payload(evt.metadata)
    if workload_snapshots:
        items.append(
            evidence_item(
                evt,
                source="metadata",
                name="current_workload_snapshots",
                value=workload_snapshots,
                summary=f"{target_summary} Workload snapshot 목록 근거입니다.",
            )
        )
    workload_snapshot = current_workload_snapshot_payload(evt.metadata)
    if workload_snapshot:
        items.append(
            evidence_item(
                evt,
                source="metadata",
                name="current_workload_snapshot",
                value=workload_snapshot,
                summary=f"{target_summary} Workload snapshot 상세 근거입니다.",
            )
        )
    service_selector_matches = metadata_list_payload(evt.metadata, "service_selector_matches")
    if service_selector_matches:
        items.append(
            evidence_item(
                evt,
                source="metadata",
                name="service_selector_matches",
                value=service_selector_matches,
                summary=f"{target_summary} Service selector와 Pod labels 매칭 근거입니다.",
            )
        )
    endpoint_slice_ready_endpoints = metadata_list_payload(
        evt.metadata,
        "endpoint_slice_ready_endpoints",
    )
    if endpoint_slice_ready_endpoints:
        items.append(
            evidence_item(
                evt,
                source="metadata",
                name="endpoint_slice_ready_endpoints",
                value=endpoint_slice_ready_endpoints,
                summary=f"{target_summary} EndpointSlice ready endpoint 근거입니다.",
            )
        )
    referenced_config_objects = metadata_list_payload(evt.metadata, "referenced_config_objects")
    if referenced_config_objects:
        items.append(
            evidence_item(
                evt,
                source="metadata",
                name="referenced_config_objects",
                value=referenced_config_objects,
                summary=f"{target_summary} ConfigMap/Secret reference 근거입니다.",
            )
        )
    resource_quotas = metadata_list_payload(evt.metadata, "resource_quotas")
    if resource_quotas:
        items.append(
            evidence_item(
                evt,
                source="metadata",
                name="resource_quotas",
                value=resource_quotas,
                summary=f"{target_summary} ResourceQuota 근거입니다.",
            )
        )
    change_context = collect_change_context(
        evt,
        resource_kind=resource_kind,
        resource_name=resource_name,
        namespace=namespace,
    )
    if change_context:
        items.append(
            evidence_item(
                evt,
                source="metadata",
                name="change_context",
                value=change_context,
                summary=f"{target_summary} Change context 근거입니다.",
            )
        )
    return items


def compact_kubernetes_value(
    kubernetes: dict,
    *,
    namespace: str | None,
    resource_kind: str,
    resource_name: str,
) -> dict:
    selected_pods = select_related_pods(kubernetes, namespace, resource_name)
    selected_pod_names = {
        str(pod.get("name")) for pod in selected_pods if pod.get("name") not in (None, "")
    }
    selected_events = select_related_events(
        kubernetes,
        namespace=namespace,
        resource_name=resource_name,
        selected_pod_names=selected_pod_names,
    )
    value: dict = {}
    for key in ("cluster", "resource", "symptom", "severity", EVIDENCE_LINEAGE_KEY):
        item = kubernetes.get(key)
        if item not in (None, "", [], {}):
            value[key] = item
    value["pods"] = selected_pods[:MAX_KUBERNETES_PODS]
    value["events"] = selected_events[:MAX_KUBERNETES_EVENTS]
    value["nodes"] = select_not_ready_nodes(kubernetes)[:MAX_KUBERNETES_NODES]
    value["workloads"] = select_related_workloads(
        kubernetes,
        namespace=namespace,
        resource_kind=resource_kind,
        resource_name=resource_name,
    )
    value["services"] = select_related_named_items(kubernetes, "services", namespace, resource_name)
    value["endpoints"] = select_related_named_items(
        kubernetes, "endpoints", namespace, resource_name
    )
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def select_related_pods(kubernetes: dict, namespace: str | None, resource_name: str) -> list[dict]:
    pods = [
        pod for pod in snapshot_dict_items(kubernetes, "pods") if same_namespace(pod, namespace)
    ]
    selected = [
        pod
        for pod in pods
        if str(pod.get("name") or "") == resource_name
        or str(pod.get("owner_name") or "") == resource_name
        or str(pod.get("workload_key") or "").endswith(f"/{resource_name}")
    ]
    return selected or pods[:MAX_KUBERNETES_PODS]


def select_related_events(
    kubernetes: dict,
    *,
    namespace: str | None,
    resource_name: str,
    selected_pod_names: set[str],
) -> list[dict]:
    events = [
        item
        for item in snapshot_dict_items(kubernetes, "events")
        if same_namespace(item, namespace)
    ]
    selected = [
        item
        for item in events
        if str(item.get("involved_name") or "") in selected_pod_names
        or str(item.get("involved_name") or "") == resource_name
    ]
    return selected or events[:MAX_KUBERNETES_EVENTS]


def select_not_ready_nodes(kubernetes: dict) -> list[dict]:
    return [
        node
        for node in snapshot_dict_items(kubernetes, "nodes")
        if node.get("ready") is False or node.get("ready") == "False"
    ]


def select_related_workloads(
    kubernetes: dict,
    *,
    namespace: str | None,
    resource_kind: str,
    resource_name: str,
) -> list[dict]:
    workloads = [
        item
        for item in snapshot_dict_items(kubernetes, "workloads")
        if same_namespace(item, namespace)
    ]
    selected = [
        item
        for item in workloads
        if str(item.get("kind") or "").casefold() == resource_kind.casefold()
        and str(item.get("name") or "") == resource_name
    ]
    return selected[:4]


def select_related_named_items(
    kubernetes: dict, key: str, namespace: str | None, resource_name: str
) -> list[dict]:
    items = [
        item for item in snapshot_dict_items(kubernetes, key) if same_namespace(item, namespace)
    ]
    selected = [item for item in items if str(item.get("name") or "").startswith(resource_name)]
    return selected[:8]


def snapshot_dict_items(kubernetes: dict, key: str) -> list[dict]:
    value = kubernetes.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def same_namespace(item: dict, namespace: str | None) -> bool:
    if not namespace:
        return True
    value = item.get("namespace")
    return value in (None, namespace)


MAX_ALERTMANAGER_ALERTS = 8


def compact_metrics_value(metrics: dict) -> dict:
    value = compact_mapping_results(
        metrics, max_results=MAX_METRIC_RESULTS, max_series=MAX_METRIC_SERIES
    )
    # Alertmanager webhook evidence(도메인 계약: metrics["alertmanager"])는
    # results 형태가 아니라 일반 압축에서 통째로 탈락한다. firing 알림의
    # 라벨/주석은 원인 판별 신호(signals)의 매칭 대상이므로 압축본에 보존한다.
    alertmanager = metrics.get("alertmanager")
    if isinstance(alertmanager, dict):
        value["alertmanager"] = compact_alertmanager_value(alertmanager)
    return value


def compact_alertmanager_value(alertmanager: dict) -> dict:
    compacted: dict = {}
    for key in ("group_key", "receiver"):
        item = alertmanager.get(key)
        if item not in (None, ""):
            compacted[key] = item
    alerts = alertmanager.get("alerts")
    if isinstance(alerts, list):
        compacted["alerts"] = [
            {
                key: alert[key]
                for key in ("status", "labels", "annotations", "startsAt")
                if isinstance(alert.get(key), (str, dict)) and alert.get(key)
            }
            for alert in alerts[:MAX_ALERTMANAGER_ALERTS]
            if isinstance(alert, dict)
        ]
    return compacted


def compact_traces_value(traces: dict) -> dict:
    return compact_mapping_results(
        traces, max_results=MAX_TRACE_RESULTS, max_series=MAX_METRIC_SERIES
    )


def compact_mapping_results(payload: dict, *, max_results: int, max_series: int) -> dict:
    value: dict = {}
    for key in (EVIDENCE_LINEAGE_KEY, "source", "status", "query_count", "result_count"):
        item = payload.get(key)
        if item not in (None, "", [], {}):
            value[key] = item
    results = payload.get("results")
    if isinstance(results, dict):
        value["results"] = {
            str(name): compact_result(result, max_series=max_series)
            for name, result in list(results.items())[:max_results]
        }
    return value or {"summary": summarize_payload(payload)}


def compact_result(result: object, *, max_series: int) -> object:
    if isinstance(result, dict):
        compacted: dict = {}
        for key, value in result.items():
            if key in {"data", "result", "values", "streams"} and isinstance(value, list):
                compacted[key] = [
                    compact_result(item, max_series=max_series) for item in value[:max_series]
                ]
            elif isinstance(value, str):
                compacted[key] = trim_text(value)
            elif isinstance(value, (dict, list)):
                compacted[key] = summarize_payload(value)
            else:
                compacted[key] = value
        return compacted
    if isinstance(result, list):
        return [compact_result(item, max_series=max_series) for item in result[:max_series]]
    if isinstance(result, str):
        return trim_text(result)
    return result


def compact_log_entries(entries: list[dict]) -> list[dict]:
    return [compact_log_entry(entry) for entry in entries[:MAX_LOG_ENTRIES]]


def compact_log_entry(entry: dict) -> dict:
    compacted = {
        key: trim_text(value) if isinstance(value, str) else value
        for key, value in entry.items()
        if key not in {"streams"}
    }
    streams = entry.get("streams")
    if isinstance(streams, list):
        compacted["streams"] = [
            compact_log_stream(stream)
            for stream in streams[:MAX_LOG_STREAMS]
            if isinstance(stream, dict)
        ]
    return compacted


def compact_log_stream(stream: dict) -> dict:
    compacted = {key: value for key, value in stream.items() if key != "values"}
    values = stream.get("values")
    if isinstance(values, list):
        compacted["values"] = [
            {
                **{key: value for key, value in sample.items() if key != "line"},
                "line": trim_text(str(sample.get("line") or "")),
            }
            for sample in values[:MAX_LOG_VALUES]
            if isinstance(sample, dict)
        ]
    return compacted


def trim_text(value: str) -> str:
    if len(value) <= MAX_TEXT_LENGTH:
        return value
    return f"{value[:MAX_TEXT_LENGTH]}..."


def summarize_payload(payload: object) -> dict:
    if isinstance(payload, dict):
        return {"type": "object", "keys": sorted(str(key) for key in payload.keys())[:20]}
    if isinstance(payload, list):
        return {"type": "list", "count": len(payload)}
    return {"type": type(payload).__name__}
