from __future__ import annotations

from packages.config.settings import env
from packages.contracts.event_bus.interfaces import JsonObject

# evidence job 튜닝값 — env 미설정 시 기존 기본값과 동일한 기본값이 적용됨(배포 호환)
DEFAULT_EVIDENCE_JOB_LEASE_SECONDS_ENV = "EVIDENCE_JOB_LEASE_SECONDS"  # 잡 리스 유지 초(기본 60)
DEFAULT_EVIDENCE_JOB_LEASE_SECONDS = int(env(DEFAULT_EVIDENCE_JOB_LEASE_SECONDS_ENV, "60"))
DEFAULT_EVIDENCE_SOURCE_ID = "cluster-snapshot"
DEFAULT_PENDING_EVIDENCE_EVENT_TTL_SECONDS_ENV = (
    "PENDING_EVIDENCE_EVENT_TTL_SECONDS"  # pending 창 회수 TTL 초(기본 120)
)
DEFAULT_PENDING_EVIDENCE_EVENT_TTL_SECONDS = int(
    env(DEFAULT_PENDING_EVIDENCE_EVENT_TTL_SECONDS_ENV, "120")
)
EVIDENCE_FAILURE_POLICY_STRICT = "strict"
EVIDENCE_JOB_STATUS_COMPLETED = "completed"
EVIDENCE_JOB_STATUS_FAILED = "failed"
EVIDENCE_JOB_STATUS_LEASED = "leased"
EVIDENCE_JOB_STATUS_QUEUED = "queued"
PENDING_EVIDENCE_EVENT_ID_PREFIX = "pending:"
TERMINAL_EVIDENCE_JOB_STATUSES = {
    EVIDENCE_JOB_STATUS_COMPLETED,
    EVIDENCE_JOB_STATUS_FAILED,
}
COLLECTION_STATUS_KEY = "collection_status"
COLLECTION_PROVIDERS_KEY = "providers"
COLLECTION_COMPLETED = "completed"
COLLECTION_PARTIAL = "partial"
COLLECTION_UNAVAILABLE = "unavailable"
COLLECTION_NOT_QUERIED = "not_queried"
COLLECTION_STATES = {
    COLLECTION_COMPLETED,
    COLLECTION_PARTIAL,
    COLLECTION_UNAVAILABLE,
    COLLECTION_NOT_QUERIED,
}
COLLECTION_COUNT_FIELDS = (
    "query_count",
    "completed_query_count",
    "failed_query_count",
)
MAX_COLLECTION_REASON_CODES = 8
MAX_COLLECTION_REASON_CODE_LENGTH = 120
NO_PROVIDER_RESULTS_REASON = "no_provider_results"
PROVIDER_JOB_FAILED_REASON = "provider_job_failed"


def evidence_key(
    workspace_id: str,
    cluster_id: str,
    source_id: str,
    window_start: str,
) -> str:
    """Build the shared key for one evidence window."""
    return ":".join([workspace_id, cluster_id, source_id, window_start])


def evidence_job_id(
    workspace_id: str,
    cluster_id: str,
    source_id: str,
    window_start: str,
    provider_key: str,
) -> str:
    """Build the unique job id for one provider in one window."""
    return ":".join([evidence_key(workspace_id, cluster_id, source_id, window_start), provider_key])


def empty_provider_payload(provider_key: str) -> object:
    """Return the empty payload shape for a failed provider."""
    if provider_key == "logs":
        return []
    return {}


def normalize_evidence_provider_result(provider_key: str, result: JsonObject) -> JsonObject:
    """리스된 provider bucket 하나만 집계 계약에 남긴다.

    구형 agent는 bucket 내부 값만 보낼 수 있고, 잘못된 클라이언트는 여러 bucket과
    전송 메타데이터를 함께 보낼 수 있다. 두 형식을 모두 받되 리스된 provider 외 값은
    증거 본문으로 승격하지 않는다.
    """
    if provider_key in result:
        normalized: JsonObject = {provider_key: result[provider_key]}
    else:
        # Legacy agents may send the provider bucket directly. In particular,
        # Kubernetes uses its own nested ``collection_status`` for API coverage,
        # so preserve that bucket verbatim instead of confusing it with the
        # new top-level provider-health envelope.
        normalized = {provider_key: result}
    provider_status = normalized_provider_collection_status(provider_key, result)
    if provider_status:
        normalized[COLLECTION_STATUS_KEY] = {
            COLLECTION_PROVIDERS_KEY: {provider_key: provider_status}
        }
    return normalized


def normalized_provider_collection_status(
    provider_key: str,
    result: JsonObject,
) -> JsonObject:
    """Accept only the leased provider's bounded status fields."""
    collection_status = result.get(COLLECTION_STATUS_KEY)
    if not isinstance(collection_status, dict):
        return {}
    providers = collection_status.get(COLLECTION_PROVIDERS_KEY)
    if not isinstance(providers, dict):
        return {}
    raw_status = providers.get(provider_key)
    if not isinstance(raw_status, dict):
        return {}
    state = raw_status.get("status")
    if state not in COLLECTION_STATES:
        return {}
    normalized: JsonObject = {"status": state}
    source = raw_status.get("source")
    if isinstance(source, str) and source:
        normalized["source"] = source[:MAX_COLLECTION_REASON_CODE_LENGTH]
    for field_name in COLLECTION_COUNT_FIELDS:
        value = raw_status.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            normalized[field_name] = value
    reason_codes = raw_status.get("reason_codes")
    if isinstance(reason_codes, list):
        normalized["reason_codes"] = [
            reason[:MAX_COLLECTION_REASON_CODE_LENGTH]
            for reason in reason_codes[:MAX_COLLECTION_REASON_CODES]
            if isinstance(reason, str) and reason
        ]
    return normalized


def merge_evidence_provider_payload(payload: JsonObject, provider_payload: JsonObject) -> None:
    """Merge one provider payload without dropping existing metadata keys."""
    provider_payload = {
        key: value
        for key, value in provider_payload.items()
        if key != COLLECTION_STATUS_KEY
    }
    provider_metadata = provider_payload.get("metadata")
    if isinstance(provider_metadata, dict):
        current_metadata = payload.get("metadata")
        merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
        merged_metadata.update(provider_metadata)
        if isinstance(current_metadata, dict) and "rca_test" in current_metadata:
            merged_metadata["rca_test"] = current_metadata["rca_test"]
        payload["metadata"] = merged_metadata
        provider_payload = {
            key: value for key, value in provider_payload.items() if key != "metadata"
        }
    payload.update(provider_payload)


def aggregate_evidence_payload(rows: list[JsonObject]) -> JsonObject | None:
    """Merge provider job results into one evidence payload.
    Return None until the window is ready to emit.
    """
    if not rows:
        return None
    if any(row["status"] not in TERMINAL_EVIDENCE_JOB_STATUSES for row in rows):
        return None
    if any(
        row["failure_policy"] == EVIDENCE_FAILURE_POLICY_STRICT
        and row["status"] == EVIDENCE_JOB_STATUS_FAILED
        for row in rows
    ):
        return None

    first = rows[0]
    payload: JsonObject = {
        "workspace_id": first["workspace_id"],
        "cluster_id": first["cluster_id"],
        "source_id": first["source_id"],
        "window_start": first["window_start"],
        "evidence_key": first["evidence_key"],
        "agent_id": first["agent_id"],
        "kubernetes": {},
    }
    release_context = common_release_context(rows)
    if release_context:
        payload["release_context"] = release_context
        correlation_id = release_context.get("correlation_id")
        if isinstance(correlation_id, str) and correlation_id:
            payload["correlation_id"] = correlation_id
        run_id = release_context.get("rca_test_run_id")
        scenario_id = release_context.get("scenario_id")
        if isinstance(run_id, str) and run_id and isinstance(scenario_id, str) and scenario_id:
            rca_test_metadata: JsonObject = {"run_id": run_id, "scenario_id": scenario_id}
            pod_names = release_context.get("pod_names")
            if isinstance(pod_names, list):
                normalized_names = [str(name).strip() for name in pod_names if str(name).strip()]
                if normalized_names:
                    rca_test_metadata["pod_names"] = list(dict.fromkeys(normalized_names))[:32]
            payload["metadata"] = {"rca_test": rca_test_metadata}
    provider_statuses: dict[str, JsonObject] = {}
    for row in rows:
        provider_key = str(row["provider_key"])
        if row["status"] == EVIDENCE_JOB_STATUS_COMPLETED and isinstance(row["result"], dict):
            normalized_result = normalize_evidence_provider_result(provider_key, row["result"])
            provider_payload = normalized_result.get(provider_key)
            reported_status = normalized_provider_collection_status(
                provider_key,
                normalized_result,
            )
            merge_evidence_provider_payload(
                payload,
                normalized_result,
            )
            provider_statuses[provider_key] = resolved_provider_collection_status(
                provider_key,
                provider_payload,
                reported_status,
            )
        elif row["status"] == EVIDENCE_JOB_STATUS_FAILED:
            payload.setdefault(provider_key, empty_provider_payload(provider_key))
            provider_statuses[provider_key] = {
                "status": COLLECTION_UNAVAILABLE,
                "reason_codes": [PROVIDER_JOB_FAILED_REASON],
            }
    payload[COLLECTION_STATUS_KEY] = aggregate_collection_status(provider_statuses)
    promote_release_target(payload, release_context)
    return payload


def resolved_provider_collection_status(
    provider_key: str,
    provider_payload: object,
    reported_status: JsonObject,
) -> JsonObject:
    """Reconcile agent status with actual payload, conservatively handling legacy agents."""
    has_results = provider_payload_has_results(provider_key, provider_payload)
    state = reported_status.get("status")
    if state in {COLLECTION_UNAVAILABLE, COLLECTION_NOT_QUERIED}:
        return dict(reported_status)
    if not has_results:
        status = dict(reported_status)
        status["status"] = COLLECTION_UNAVAILABLE
        status["reason_codes"] = merge_reason_codes(
            status.get("reason_codes"),
            NO_PROVIDER_RESULTS_REASON,
        )
        return status
    if state in {COLLECTION_COMPLETED, COLLECTION_PARTIAL}:
        return dict(reported_status)
    return {"status": COLLECTION_COMPLETED, "reason_codes": []}


def provider_payload_has_results(provider_key: str, provider_payload: object) -> bool:
    """Distinguish successful empty query results from an unqueried empty envelope."""
    if provider_key == "logs":
        return isinstance(provider_payload, list) and bool(provider_payload)
    if not isinstance(provider_payload, dict):
        return False
    if provider_key == "metrics":
        alertmanager = provider_payload.get("alertmanager")
        if isinstance(alertmanager, dict) and bool(alertmanager):
            return True
        if provider_payload.get("source") == "prometheus" or "results" in provider_payload:
            results = provider_payload.get("results")
            return isinstance(results, dict) and bool(results)
    if provider_key == "traces" and (
        provider_payload.get("source") == "tempo" or "results" in provider_payload
    ):
        results = provider_payload.get("results")
        return isinstance(results, dict) and bool(results)
    return any(
        value not in (None, "", [], {})
        for key, value in provider_payload.items()
        if key != "_lineage"
    )


def merge_reason_codes(current: object, reason: str) -> list[str]:
    values = (
        [value for value in current if isinstance(value, str) and value]
        if isinstance(current, list)
        else []
    )
    if reason not in values:
        values.append(reason)
    return values[:MAX_COLLECTION_REASON_CODES]


def aggregate_collection_status(statuses: dict[str, JsonObject]) -> JsonObject:
    completed = sorted(
        key for key, status in statuses.items() if status.get("status") == COLLECTION_COMPLETED
    )
    partial = sorted(
        key for key, status in statuses.items() if status.get("status") == COLLECTION_PARTIAL
    )
    failed = sorted(
        key
        for key, status in statuses.items()
        if status.get("status") in {COLLECTION_UNAVAILABLE, COLLECTION_NOT_QUERIED}
    )
    return {
        "complete": len(completed) == len(statuses),
        "completed_providers": completed,
        "partial_providers": partial,
        "failed_providers": failed,
        "pending_providers": [],
        COLLECTION_PROVIDERS_KEY: statuses,
    }


def common_release_context(rows: list[JsonObject]) -> JsonObject:
    """provider job snapshot에 보존된 공통 수집 문맥을 evidence body로 승격한다."""
    contexts: list[JsonObject] = []
    for row in rows:
        provider_policy = row.get("provider_policy")
        if not isinstance(provider_policy, dict):
            continue
        context = provider_policy.get("release_context")
        if isinstance(context, dict) and context:
            contexts.append(context)
    if not contexts:
        return {}
    first = contexts[0]
    return first if all(context == first for context in contexts[1:]) else {}


def promote_release_target(payload: JsonObject, release_context: JsonObject) -> None:
    """Keep the server-owned Deployment target instead of the Pod's ReplicaSet owner."""
    if release_context.get("evidence_scope") != "rca_test_run":
        return
    kind = release_context.get("resource_kind")
    name = release_context.get("resource_name")
    namespace = release_context.get("namespace")
    kubernetes = payload.get("kubernetes")
    if not isinstance(kubernetes, dict) or not isinstance(kind, str) or not isinstance(name, str):
        return
    if not kind or not name:
        return
    kubernetes["resource"] = {
        "kind": kind,
        "name": name,
        "namespace": namespace if isinstance(namespace, str) and namespace else None,
    }
