"""Persisted debug-query adapter for authenticated browser log streams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import HTTPException, Request

from domains.command.debug_queries import (
    LOG_STREAM_QUERY_METADATA_KEY,
    LOG_STREAM_QUERY_NAME_PREFIX,
    QueuedDebugQuery,
    queue_debug_query,
)
from domains.identity.dependencies import require_cluster_access
from packages.config.constants import Command, CommandStatus
from packages.contracts.gateway import params as gateway_params
from packages.contracts.gateway.requests import AgentDebugQueryRequest
from packages.contracts.identity import Permission
from packages.contracts.log_stream import (
    LogStreamConnected,
    LogStreamDiagnostic,
    LogStreamEnd,
    LogStreamError,
    LogStreamLog,
    LogStreamPodAdded,
    LogStreamPodRemoved,
    ScheduledRunLifecycleEvent,
    ScheduledWorkloadRun,
    ScheduledWorkloadRunCatalog,
)
from packages.contracts.parity import ClusterScope, ResourceRef
from packages.security.log_lines import redact_log_line, truncate_log_line

WorkloadLogKind = Literal["deployments", "statefulsets", "daemonsets"]

WORKLOAD_KIND_NAMES: dict[WorkloadLogKind, str] = {
    "deployments": "Deployment",
    "statefulsets": "StatefulSet",
    "daemonsets": "DaemonSet",
}
LOG_STREAM_PROTOCOL = "log-stream.v1"
LOG_QUERY_RANGE_SECONDS = 30
LOG_STREAM_BATCH_LIMIT = 20
LOG_STREAM_RESULT_TIMEOUT_SECONDS = 20.0
LOG_STREAM_STATUS_POLL_SECONDS = 0.25
LOG_STREAM_BATCH_INTERVAL_SECONDS = 2.0
LOG_STREAM_DISCONNECT_POLL_SECONDS = 0.1
MAX_DEDUPE_IDS = 2000
MAX_AI_LOG_EVIDENCE = 20
TARGET_NOT_FOUND = "log stream target not found"
KUBERNETES_NAME_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
KUBERNETES_NAMESPACE_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
KUBERNETES_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


@dataclass(frozen=True)
class LogStreamTarget:
    target_type: Literal["pod", "workload", "scheduled_run"]
    cluster_id: str
    namespace: str
    name: str
    kind: str
    resource_type: str
    pods: tuple[str, ...]
    containers: tuple[str, ...] = ()
    container: str | None = None
    uid: str | None = None
    owner_kind: str | None = None
    owner_name: str | None = None
    owner_uid: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "protocol": LOG_STREAM_PROTOCOL,
            "target_type": self.target_type,
            "cluster_id": self.cluster_id,
            "namespace": self.namespace,
            "name": self.name,
            "kind": self.kind,
            "resource_type": self.resource_type,
            "pods": list(self.pods),
            "containers": list(self.containers),
            "container": self.container,
            "uid": self.uid,
            "owner_kind": self.owner_kind,
            "owner_name": self.owner_name,
            "owner_uid": self.owner_uid,
        }

    def link(self) -> str:
        detail = "/".join((self.kind, self.namespace, self.name))
        return "/resources?" + urlencode(
            {
                "clusters": self.cluster_id,
                gateway_params.RESOURCE_TYPES_QUERY: self.resource_type,
                "detail": detail,
            }
        )


@dataclass(frozen=True)
class PersistedLogEvidence:
    event: LogStreamLog
    link: str


class BoundedDedupe:
    def __init__(self, max_items: int = MAX_DEDUPE_IDS) -> None:
        self.max_items = max_items
        self.ids: set[str] = set()
        self.order: deque[str] = deque()

    def add(self, value: str) -> bool:
        if value in self.ids:
            return False
        self.ids.add(value)
        self.order.append(value)
        while len(self.order) > self.max_items:
            self.ids.discard(self.order.popleft())
        return True


def resolve_pod_target(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
    name: str,
    container: str | None,
) -> LogStreamTarget:
    _require_log_access(db, current, workspace_id, cluster_id)
    resource = _inventory_resource(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="pod",
        kind="Pod",
        namespace=namespace,
        name=name,
    )
    if resource is None or not _container_exists(resource, container):
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    return LogStreamTarget(
        target_type="pod",
        cluster_id=cluster_id,
        namespace=namespace,
        name=name,
        kind="Pod",
        resource_type="pod",
        pods=(name,),
        containers=_container_names(resource),
        container=container,
        uid=str(resource.get("uid") or "") or None,
    )


def resolve_workload_target(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    kind: WorkloadLogKind,
    namespace: str,
    name: str,
    container: str | None,
) -> LogStreamTarget:
    _require_log_access(db, current, workspace_id, cluster_id)
    kubernetes_kind = WORKLOAD_KIND_NAMES[kind]
    resource = _inventory_resource(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="workload",
        kind=kubernetes_kind,
        namespace=namespace,
        name=name,
    )
    if resource is None:
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    related_reader = getattr(db, "list_related_inventory_resources", None)
    if not callable(related_reader):
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    related = related_reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource=resource,
        limit=1000,
    )
    pods = [
        pod
        for pod in (related.get("pods") if isinstance(related, dict) else []) or []
        if isinstance(pod, dict)
        and str(pod.get("cluster_id") or "") == cluster_id
        and str(pod.get("namespace") or "") == namespace
        and str(pod.get("resource_type") or "").lower() == "pod"
        and str(pod.get("kind") or "").lower() == "pod"
    ]
    if container is not None and not any(_container_exists(pod, container) for pod in pods):
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    pod_names = tuple(
        sorted({str(pod.get("name") or "") for pod in pods if str(pod.get("name") or "")})
    )
    return LogStreamTarget(
        target_type="workload",
        cluster_id=cluster_id,
        namespace=namespace,
        name=name,
        kind=kubernetes_kind,
        resource_type="workload",
        pods=pod_names,
        containers=tuple(
            sorted({container for pod in pods for container in _container_names(pod)})
        ),
        container=container,
        uid=str(resource.get("uid") or "") or None,
    )


def scheduled_workload_run_catalog(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    owner_kind: str,
    namespace: str,
    owner_name: str,
) -> ScheduledWorkloadRunCatalog:
    """Return the retained, server-authoritative run catalog for one owner.

    The repository performs two bounded queries for the entire catalog.  This
    projection repeats every owner boundary check before exposing a run key so
    a malformed collector payload cannot cross owner or cluster scope.
    """

    catalog, _targets = _scheduled_run_projection(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        owner_kind=owner_kind,
        namespace=namespace,
        owner_name=owner_name,
        require_evidence=False,
    )
    return catalog


def resolve_scheduled_run_target(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    owner_kind: str,
    namespace: str,
    owner_name: str,
    run_key: str,
) -> LogStreamTarget:
    _catalog, targets = _scheduled_run_projection(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        owner_kind=owner_kind,
        namespace=namespace,
        owner_name=owner_name,
        require_evidence=True,
    )
    target = targets.get(run_key)
    if target is None:
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    return target


def _scheduled_run_projection(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    owner_kind: str,
    namespace: str,
    owner_name: str,
    require_evidence: bool,
) -> tuple[ScheduledWorkloadRunCatalog, dict[str, LogStreamTarget]]:
    _require_inventory_access(db, current, workspace_id, cluster_id)
    can_view_logs = _has_evidence_access(db, current, workspace_id, cluster_id)
    if require_evidence and not can_view_logs:
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    owner = _inventory_resource(
        db,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type="workload",
        kind=owner_kind,
        namespace=namespace,
        name=owner_name,
    )
    owner_uid = str(owner.get("uid") or "") if owner else ""
    owner_summary = owner.get("summary") if owner and isinstance(owner.get("summary"), dict) else {}
    raw_run_kinds = owner_summary.get("scheduled_run_kinds")
    run_kinds = tuple(
        sorted(
            {
                str(kind)
                for kind in (raw_run_kinds if isinstance(raw_run_kinds, list) else [])
                if isinstance(kind, str) and kind
            }
        )
    )
    reader = getattr(db, "list_scheduled_run_inventory", None)
    if owner is None or not owner_uid or not run_kinds or not callable(reader):
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)

    raw = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        owner_kind=owner_kind,
        owner_name=owner_name,
        owner_uid=owner_uid,
        run_kinds=run_kinds,
        limit=100,
        pod_limit=1000,
    )
    rows = raw.get("runs") if isinstance(raw, dict) and isinstance(raw.get("runs"), list) else []
    pod_rows = (
        raw.get("pods") if isinstance(raw, dict) and isinstance(raw.get("pods"), list) else []
    )
    reasons: set[str] = set()
    if bool(raw.get("runs_truncated")):
        reasons.add("run_limit_reached")
    if bool(raw.get("pods_truncated")):
        reasons.add("pod_limit_reached")
    reasons.update(
        str(reason)
        for reason in (raw.get("partial_reason_codes") or ())
        if isinstance(reason, str) and reason
    )

    runs: list[ScheduledWorkloadRun] = []
    lifecycle: list[ScheduledRunLifecycleEvent] = []
    targets: dict[str, LogStreamTarget] = {}
    for row in rows:
        if not isinstance(row, dict) or not _scheduled_run_belongs_to_owner(
            row,
            cluster_id=cluster_id,
            namespace=namespace,
            owner_kind=owner_kind,
            owner_name=owner_name,
            owner_uid=owner_uid,
            run_kinds=run_kinds,
        ):
            reasons.add("invalid_run_excluded")
            continue
        run_uid = str(row.get("uid") or "")
        run_name = str(row.get("name") or "")
        run_kind = str(row.get("kind") or "")
        matching_pods = tuple(
            pod
            for pod in pod_rows
            if isinstance(pod, dict)
            and str(pod.get("cluster_id") or "") == cluster_id
            and str(pod.get("namespace") or "") == namespace
            and _summary_owner_matches(pod, uid=run_uid, kind=run_kind, name=run_name)
        )
        pod_names = tuple(
            sorted({str(pod.get("name") or "") for pod in matching_pods if pod.get("name")})
        )
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        active_count = _non_negative_int(summary.get("active"))
        succeeded_count = _non_negative_int(summary.get("succeeded"))
        failed_count = _non_negative_int(summary.get("failed"))
        desired = _optional_non_negative_int(summary.get("completions"))
        phase = _scheduled_run_phase(
            active=active_count,
            succeeded=succeeded_count,
            failed=failed_count,
            desired=desired,
        )
        pod_succeeded = sum(_pod_phase(pod) == "succeeded" for pod in matching_pods)
        pod_failed = sum(_pod_phase(pod) == "failed" for pod in matching_pods)
        pod_running = sum(_pod_phase(pod) == "running" for pod in matching_pods)
        run = ScheduledWorkloadRun(
            run_key=run_uid,
            resource=_resource_ref(row),
            phase=phase,
            active=active_count > 0,
            scheduled_at=_optional_iso(summary.get("creation_timestamp")),
            started_at=_optional_iso(summary.get("start_time")),
            finished_at=_optional_iso(summary.get("completion_time")),
            desired=desired,
            succeeded=succeeded_count,
            failed=failed_count,
            pod_total=len(matching_pods),
            pod_succeeded=pod_succeeded,
            pod_failed=pod_failed,
            pod_running=pod_running,
            next_step=_scheduled_run_next_step(
                phase=phase,
                can_view_logs=can_view_logs,
                has_container_outcome=(pod_succeeded + pod_failed + pod_running) > 0,
            ),
            observed_at=_optional_iso(row.get("observed_at")),
        )
        runs.append(run)
        lifecycle.extend(_scheduled_run_lifecycle(run))
        targets[run_uid] = LogStreamTarget(
            target_type="scheduled_run",
            cluster_id=cluster_id,
            namespace=namespace,
            name=run_name,
            kind=run_kind,
            resource_type="workload",
            pods=pod_names,
            containers=tuple(
                sorted({container for pod in matching_pods for container in _container_names(pod)})
            ),
            uid=run_uid,
            owner_kind=owner_kind,
            owner_name=owner_name,
            owner_uid=owner_uid,
        )

    default_run = next((run.run_key for run in runs if run.active), None)
    if default_run is None and runs:
        default_run = runs[0].run_key
    return (
        ScheduledWorkloadRunCatalog(
            scope=ClusterScope(
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                namespaces=(namespace,),
                freshness="partial" if reasons else "live",
            ),
            owner=_resource_ref(owner),
            runs=tuple(runs),
            lifecycle=tuple(
                sorted(lifecycle, key=lambda event: (event.occurred_at, event.event_id))
            ),
            default_run_key=default_run,
            complete=not reasons,
            reason_codes=tuple(sorted(reasons)),
        ),
        targets,
    )


def queue_log_query(
    db: Any,
    *,
    workspace_id: str,
    user_id: str,
    target: LogStreamTarget,
    correlation_id: str | None = None,
) -> QueuedDebugQuery:
    query_name = f"{LOG_STREAM_QUERY_NAME_PREFIX}{uuid.uuid4().hex}"
    query = {
        "source": "loki",
        "name": query_name,
        "description": "Opsia bounded browser log stream batch",
        "query": safe_logql(target),
        "range_seconds": LOG_QUERY_RANGE_SECONDS,
        LOG_STREAM_QUERY_METADATA_KEY: target.metadata(),
    }
    queued = queue_debug_query(
        db,
        AgentDebugQueryRequest(
            cluster_id=target.cluster_id,
            query=query,
            reason="bounded browser log stream",
        ),
        workspace_id=workspace_id,
        requested_by=user_id,
        correlation_id=correlation_id,
    )
    if not queued.inserted:
        raise RuntimeError("failed to persist unique log stream command")
    return queued


def safe_logql(target: LogStreamTarget) -> str:
    labels = [f"k8s_namespace_name={json.dumps(target.namespace)}"]
    if target.pods:
        if len(target.pods) == 1:
            labels.append(f"k8s_pod_name={json.dumps(target.pods[0])}")
        else:
            alternatives = "|".join(_safe_pod_regex(name) for name in target.pods)
            labels.append(f"k8s_pod_name=~{json.dumps(f'^(?:{alternatives})$')}")
    else:
        # Kubernetes names cannot contain underscores. This selector is a safe,
        # guaranteed no-match query that still yields a persisted stream handle.
        labels.append('k8s_pod_name="__opsia_no_pod__"')
    if target.container is not None:
        labels.append(f"k8s_container_name={json.dumps(target.container)}")
    return "{" + ",".join(labels) + "}"


async def stream_log_events(
    request: Request,
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    initial_target: LogStreamTarget,
    initial_query: QueuedDebugQuery,
):
    yield LogStreamConnected(
        stream_id=initial_query.command_id,
        containers=initial_target.containers,
    )
    known_pods = set(initial_target.pods)
    for pod in sorted(known_pods):
        yield LogStreamPodAdded(pod=pod)
    if not known_pods:
        yield LogStreamEnd(
            reason="no_pods",
            diagnostic=empty_log_diagnostic(reason="no_matching_pods"),
        )
        return

    target = initial_target
    query = initial_query
    dedupe = BoundedDedupe()
    emitted_line_count = 0
    for batch_index in range(LOG_STREAM_BATCH_LIMIT):
        if await _is_disconnected(request):
            return
        row = await _wait_for_command(request, db, workspace_id, query, current, target)
        if row is None:
            if await _is_disconnected(request):
                return
            yield LogStreamError(code="agent_timeout", retryable=True)
            return
        if str(row.get("status") or "") != CommandStatus.COMPLETED:
            yield LogStreamError(code="agent_failed", retryable=True)
            return

        try:
            target = _reresolve_target(db, current, workspace_id, target)
        except HTTPException:
            yield LogStreamError(code="target_unavailable", retryable=False)
            return

        current_pods = set(target.pods)
        for pod in sorted(current_pods - known_pods):
            yield LogStreamPodAdded(pod=pod)
        for pod in sorted(known_pods - current_pods):
            yield LogStreamPodRemoved(pod=pod)
        known_pods = current_pods

        for evidence in extract_log_evidence(row, target=target, limit=1000):
            if dedupe.add(evidence.event.id):
                emitted_line_count += 1
                yield evidence.event

        if batch_index + 1 >= LOG_STREAM_BATCH_LIMIT:
            yield LogStreamEnd(
                reason="window_complete",
                diagnostic=(
                    empty_log_diagnostic(reason="no_log_lines") if emitted_line_count == 0 else None
                ),
            )
            return
        if await _wait_or_disconnect(request, LOG_STREAM_BATCH_INTERVAL_SECONDS):
            return
        if not known_pods:
            yield LogStreamEnd(
                reason="no_pods",
                diagnostic=empty_log_diagnostic(reason="no_matching_pods"),
            )
            return
        try:
            target = _reresolve_target(db, current, workspace_id, target)
            query = queue_log_query(
                db,
                workspace_id=workspace_id,
                user_id=str(current.user_id),
                target=target,
                correlation_id=initial_query.correlation_id,
            )
        except HTTPException:
            yield LogStreamError(code="target_unavailable", retryable=False)
            return
        except RuntimeError:
            yield LogStreamError(code="stream_unavailable", retryable=True)
            return


def empty_log_diagnostic(
    *,
    reason: Literal["no_matching_pods", "no_log_lines"],
) -> LogStreamDiagnostic:
    """Describe an empty agent result without exposing a local cluster escape hatch."""

    return LogStreamDiagnostic(code=reason)


async def read_log_stream_evidence(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    stream_id: str,
    limit: int = MAX_AI_LOG_EVIDENCE,
) -> list[PersistedLogEvidence]:
    row = await db.get_agent_command(stream_id, workspace_id)
    if row is None or not _owned_log_query(row, current=current, expected=None):
        return []
    target = _target_from_command(row)
    correlation_id = row.get("correlation_id")
    if (
        target is None
        or str(row.get("cluster_id") or "") != target.cluster_id
        or not isinstance(correlation_id, str)
        or not correlation_id
        or str(row.get("status") or "") != CommandStatus.COMPLETED
    ):
        return []
    try:
        current_target = _reresolve_target(db, current, workspace_id, target)
    except HTTPException:
        return []
    reader = getattr(db, "list_agent_commands_by_correlation", None)
    if not callable(reader):
        return []
    rows = await reader(
        workspace_id,
        correlation_id,
        limit=LOG_STREAM_BATCH_LIMIT,
    )
    if not isinstance(rows, list) or not rows or len(rows) > LOG_STREAM_BATCH_LIMIT:
        return []

    logical_identity = _logical_target_identity(target)
    if not _logical_target_matches(target, current_target):
        return []
    completed: list[tuple[dict[str, Any], LogStreamTarget]] = []
    command_ids: set[str] = set()
    for candidate in rows:
        if not isinstance(candidate, dict):
            return []
        command_id = candidate.get("command_id")
        candidate_target = _target_from_command(candidate)
        if (
            not isinstance(command_id, str)
            or not command_id
            or command_id in command_ids
            or candidate.get("correlation_id") != correlation_id
            or not _owned_log_query(candidate, current=current, expected=None)
            or candidate_target is None
            or str(candidate.get("cluster_id") or "") != candidate_target.cluster_id
            or _logical_target_identity(candidate_target) != logical_identity
        ):
            return []
        command_ids.add(command_id)
        if str(candidate.get("status") or "") == CommandStatus.COMPLETED:
            completed.append((candidate, candidate_target))
    if stream_id not in command_ids:
        return []

    unique: list[PersistedLogEvidence] = []
    seen = BoundedDedupe(max_items=max(1, min(limit, MAX_AI_LOG_EVIDENCE)))
    evidence_limit = max(1, min(limit, MAX_AI_LOG_EVIDENCE))
    for candidate, candidate_target in completed:
        for item in extract_log_evidence(
            candidate,
            target=candidate_target,
            limit=evidence_limit - len(unique),
        ):
            if seen.add(item.event.id):
                unique.append(item)
            if len(unique) >= evidence_limit:
                return unique
    return unique


def _logical_target_identity(target: LogStreamTarget) -> tuple[str, ...]:
    return (
        target.target_type,
        target.cluster_id,
        target.namespace,
        target.name,
        target.kind,
        target.resource_type,
        target.container or "",
        target.uid or "",
        target.owner_kind if target.target_type == "scheduled_run" and target.owner_kind else "",
        target.owner_name if target.target_type == "scheduled_run" and target.owner_name else "",
        target.owner_uid if target.target_type == "scheduled_run" and target.owner_uid else "",
    )


def _logical_target_matches(expected: LogStreamTarget, actual: LogStreamTarget) -> bool:
    if expected.uid is None:
        actual = LogStreamTarget(
            target_type=actual.target_type,
            cluster_id=actual.cluster_id,
            namespace=actual.namespace,
            name=actual.name,
            kind=actual.kind,
            resource_type=actual.resource_type,
            pods=actual.pods,
            container=actual.container,
            uid=None,
            owner_kind=actual.owner_kind,
            owner_name=actual.owner_name,
            owner_uid=actual.owner_uid,
        )
    return _logical_target_identity(expected) == _logical_target_identity(actual)


def extract_log_evidence(
    row: dict[str, Any],
    *,
    target: LogStreamTarget,
    limit: int,
) -> list[PersistedLogEvidence]:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    batches = result.get("result") if isinstance(result, dict) else None
    if not isinstance(batches, list):
        return []
    expected_query = _command_query(row)
    expected_name = str(expected_query.get("name") or "")
    expected_logql = str(expected_query.get("query") or "")
    evidence: list[PersistedLogEvidence] = []
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        if (
            batch.get("source") != "loki"
            or batch.get("query_name") != expected_name
            or batch.get("query") != expected_logql
            or not isinstance(batch.get("redaction_summary"), dict)
            or batch["redaction_summary"].get("applied") is not True
        ):
            continue
        streams = batch.get("streams")
        if not isinstance(streams, list):
            continue
        for stream_payload in streams:
            evidence.extend(
                _stream_evidence(stream_payload, target=target, limit=limit - len(evidence))
            )
            if len(evidence) >= limit:
                return evidence
    return evidence


def _stream_evidence(
    payload: object,
    *,
    target: LogStreamTarget,
    limit: int,
) -> list[PersistedLogEvidence]:
    if limit <= 0 or not isinstance(payload, dict):
        return []
    labels = payload.get("stream") if isinstance(payload.get("stream"), dict) else {}
    namespace = _stream_label(labels, ("k8s_namespace_name", "namespace"))
    pod = _stream_label(labels, ("k8s_pod_name", "pod", "pod_name", "kubernetes_pod_name"))
    container = _stream_label(
        labels,
        ("k8s_container_name", "container", "container_name", "kubernetes_container_name"),
    )
    if (
        namespace != target.namespace
        or pod not in set(target.pods)
        or not container
        or (target.container is not None and container != target.container)
    ):
        return []
    values = payload.get("values")
    if not isinstance(values, list):
        return []
    evidence: list[PersistedLogEvidence] = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("line"), str):
            continue
        raw_timestamp = value.get("timestamp")
        observed_at = _observed_at(raw_timestamp)
        if observed_at is None:
            continue
        redacted = redact_log_line(value["line"])
        line, defense_truncated = truncate_log_line(redacted)
        line_truncated = bool(value.get("line_truncated")) or defense_truncated
        line_id = _line_id(
            target.cluster_id,
            namespace,
            pod,
            container,
            str(raw_timestamp),
            line,
        )
        event = LogStreamLog(
            id=line_id,
            observed_at=observed_at,
            pod=pod,
            container=container,
            line=line,
            line_truncated=line_truncated,
        )
        evidence.append(PersistedLogEvidence(event=event, link=target.link()))
        if len(evidence) >= limit:
            return evidence
    return evidence


async def _wait_for_command(
    request: Request,
    db: Any,
    workspace_id: str,
    query: QueuedDebugQuery,
    current: Any,
    target: LogStreamTarget,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + LOG_STREAM_RESULT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if await _is_disconnected(request):
            return None
        row = await db.get_agent_command(query.command_id, workspace_id)
        if row is not None and not _owned_log_query(row, current=current, expected=query):
            return None
        if row is not None and str(row.get("cluster_id") or "") != target.cluster_id:
            return None
        if row is not None and str(row.get("status") or "") in {
            CommandStatus.COMPLETED,
            CommandStatus.FAILED,
        }:
            return row
        if await _wait_or_disconnect(request, LOG_STREAM_STATUS_POLL_SECONDS):
            return None
    return None


def _owned_log_query(
    row: dict[str, Any],
    *,
    current: Any,
    expected: QueuedDebugQuery | None,
) -> bool:
    if str(row.get("action") or "") != Command.TELEMETRY_QUERY_RUN_ACTION:
        return False
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if payload.get("requested_by") != getattr(current, "user_id", None):
        return False
    if expected is not None and payload != expected.plan:
        return False
    query = _command_query(row)
    metadata = (
        query.get(LOG_STREAM_QUERY_METADATA_KEY)
        if isinstance(query.get(LOG_STREAM_QUERY_METADATA_KEY), dict)
        else {}
    )
    return metadata.get("protocol") == LOG_STREAM_PROTOCOL


def _target_from_command(row: dict[str, Any]) -> LogStreamTarget | None:
    query = _command_query(row)
    value = query.get(LOG_STREAM_QUERY_METADATA_KEY)
    if not isinstance(value, dict) or value.get("protocol") != LOG_STREAM_PROTOCOL:
        return None
    target_type = value.get("target_type")
    if target_type not in {"pod", "workload", "scheduled_run"}:
        return None
    if target_type == "pod" and (value.get("kind") != "Pod" or value.get("resource_type") != "pod"):
        return None
    if target_type == "workload" and (
        value.get("kind") not in set(WORKLOAD_KIND_NAMES.values())
        or value.get("resource_type") != "workload"
    ):
        return None
    if target_type == "scheduled_run" and (
        value.get("resource_type") != "workload"
        or not all(value.get(key) for key in ("uid", "owner_kind", "owner_name", "owner_uid"))
    ):
        return None
    try:
        target = LogStreamTarget(
            target_type=target_type,
            cluster_id=str(value["cluster_id"]),
            namespace=str(value["namespace"]),
            name=str(value["name"]),
            kind=str(value["kind"]),
            resource_type=str(value["resource_type"]),
            pods=tuple(str(pod) for pod in value.get("pods") or ()),
            containers=tuple(str(name) for name in value.get("containers") or ()),
            container=(str(value["container"]) if value.get("container") is not None else None),
            uid=(str(value["uid"]) if value.get("uid") is not None else None),
            owner_kind=(str(value["owner_kind"]) if value.get("owner_kind") is not None else None),
            owner_name=(str(value["owner_name"]) if value.get("owner_name") is not None else None),
            owner_uid=(str(value["owner_uid"]) if value.get("owner_uid") is not None else None),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not _valid_persisted_target(target):
        return None
    if (
        query.get("source") != "loki"
        or not str(query.get("name") or "").startswith(LOG_STREAM_QUERY_NAME_PREFIX)
        or query.get("query") != safe_logql(target)
        or query.get("range_seconds") != LOG_QUERY_RANGE_SECONDS
    ):
        return None
    return target


def _valid_persisted_target(target: LogStreamTarget) -> bool:
    if (
        not target.cluster_id
        or len(target.cluster_id) > 512
        or not KUBERNETES_NAMESPACE_RE.fullmatch(target.namespace)
        or len(target.namespace) > 63
        or not KUBERNETES_NAME_RE.fullmatch(target.name)
        or len(target.name) > 253
        or len(target.pods) > 1000
        or len(set(target.pods)) != len(target.pods)
        or any(len(pod) > 253 or KUBERNETES_NAME_RE.fullmatch(pod) is None for pod in target.pods)
        or len(target.containers) > 1000
        or tuple(sorted(set(target.containers))) != target.containers
        or any(
            len(container) > 63 or KUBERNETES_CONTAINER_RE.fullmatch(container) is None
            for container in target.containers
        )
        or (
            target.container is not None
            and (
                len(target.container) > 63
                or KUBERNETES_CONTAINER_RE.fullmatch(target.container) is None
            )
        )
        or any(
            value is not None and (not value or len(value) > 255)
            for value in (target.uid, target.owner_uid)
        )
        or any(
            value is not None and (len(value) > 253 or KUBERNETES_NAME_RE.fullmatch(value) is None)
            for value in (target.owner_name,)
        )
    ):
        return False
    if target.target_type == "pod":
        return target.pods == (target.name,)
    if target.target_type == "scheduled_run":
        return all((target.uid, target.owner_kind, target.owner_name, target.owner_uid))
    return True


def _command_query(row: dict[str, Any]) -> dict[str, Any]:
    plan = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    body = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    query = body.get("query")
    return query if isinstance(query, dict) else {}


def _reresolve_target(
    db: Any,
    current: Any,
    workspace_id: str,
    target: LogStreamTarget,
) -> LogStreamTarget:
    if target.target_type == "pod":
        resolved = resolve_pod_target(
            db,
            current=current,
            workspace_id=workspace_id,
            cluster_id=target.cluster_id,
            namespace=target.namespace,
            name=target.name,
            container=target.container,
        )
        return _same_inventory_generation(target, resolved)
    if target.target_type == "scheduled_run":
        if not target.owner_kind or not target.owner_name or not target.uid:
            raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
        resolved = resolve_scheduled_run_target(
            db,
            current=current,
            workspace_id=workspace_id,
            cluster_id=target.cluster_id,
            owner_kind=target.owner_kind,
            namespace=target.namespace,
            owner_name=target.owner_name,
            run_key=target.uid,
        )
        if resolved.owner_uid != target.owner_uid:
            raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
        return _same_inventory_generation(target, resolved)
    kind = next(
        (key for key, value in WORKLOAD_KIND_NAMES.items() if value == target.kind),
        None,
    )
    if kind is None:
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    resolved = resolve_workload_target(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=target.cluster_id,
        kind=kind,
        namespace=target.namespace,
        name=target.name,
        container=target.container,
    )
    return _same_inventory_generation(target, resolved)


def _same_inventory_generation(
    expected: LogStreamTarget,
    resolved: LogStreamTarget,
) -> LogStreamTarget:
    if expected.uid is not None and resolved.uid != expected.uid:
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)
    return resolved


def _inventory_resource(db: Any, **identity: Any) -> dict[str, Any] | None:
    reader = getattr(db, "get_latest_inventory_resource", None)
    if not callable(reader):
        reader = getattr(db, "get_inventory_resource", None)
    if not callable(reader):
        return None
    resource = reader(**identity)
    return resource if isinstance(resource, dict) else None


def _scheduled_run_belongs_to_owner(
    row: dict[str, Any],
    *,
    cluster_id: str,
    namespace: str,
    owner_kind: str,
    owner_name: str,
    owner_uid: str,
    run_kinds: tuple[str, ...],
) -> bool:
    return (
        bool(row.get("uid"))
        and bool(row.get("name"))
        and str(row.get("cluster_id") or "") == cluster_id
        and str(row.get("namespace") or "") == namespace
        and str(row.get("resource_type") or "").lower() == "workload"
        and str(row.get("kind") or "") in run_kinds
        and _summary_owner_matches(
            row,
            uid=owner_uid,
            kind=owner_kind,
            name=owner_name,
        )
    )


def _summary_owner_matches(row: dict[str, Any], *, uid: str, kind: str, name: str) -> bool:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    return (
        str(summary.get("owner_uid") or "") == uid
        and str(summary.get("owner_kind") or "") == kind
        and str(summary.get("owner_name") or "") == name
    )


def _resource_ref(row: dict[str, Any]) -> ResourceRef:
    api_version = str(row.get("api_version") or "")
    api_group, separator, version = api_version.partition("/")
    if not separator:
        version = api_group
        api_group = ""
    return ResourceRef(
        api_group=api_group,
        version=version,
        kind=str(row.get("kind") or ""),
        namespace=str(row.get("namespace")) if row.get("namespace") is not None else None,
        name=str(row.get("name") or ""),
        uid=str(row.get("uid") or ""),
    )


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_non_negative_int(value: Any) -> int | None:
    return None if value is None else _non_negative_int(value)


def _scheduled_run_phase(
    *, active: int, succeeded: int, failed: int, desired: int | None
) -> Literal["pending", "running", "succeeded", "failed", "unknown"]:
    if active > 0:
        return "running"
    if failed > 0:
        return "failed"
    if succeeded > 0 and (desired is None or succeeded >= desired):
        return "succeeded"
    if succeeded == 0 and failed == 0:
        return "pending"
    return "unknown"


def _pod_phase(row: dict[str, Any]) -> str:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    return str(summary.get("phase") or "").lower()


def _scheduled_run_next_step(
    *, phase: str, can_view_logs: bool, has_container_outcome: bool
) -> Literal["logs", "timeline"] | None:
    if phase != "failed":
        return None
    if can_view_logs and has_container_outcome:
        return "logs"
    return "timeline"


def _scheduled_run_lifecycle(run: ScheduledWorkloadRun) -> list[ScheduledRunLifecycleEvent]:
    events: list[ScheduledRunLifecycleEvent] = []
    kind = run.resource.kind
    if run.scheduled_at is not None:
        events.append(
            ScheduledRunLifecycleEvent(
                event_id=f"{run.run_key}:scheduled",
                run_key=run.run_key,
                resource=run.resource,
                stage="scheduled",
                occurred_at=run.scheduled_at,
                event_type="normal",
                reason=f"{kind} scheduled",
            )
        )
    if run.started_at is not None:
        events.append(
            ScheduledRunLifecycleEvent(
                event_id=f"{run.run_key}:started",
                run_key=run.run_key,
                resource=run.resource,
                stage="started",
                occurred_at=run.started_at,
                event_type="normal",
                reason=f"{kind} started",
            )
        )
    if run.finished_at is not None:
        events.append(
            ScheduledRunLifecycleEvent(
                event_id=f"{run.run_key}:finished",
                run_key=run.run_key,
                resource=run.resource,
                stage="finished",
                occurred_at=run.finished_at,
                event_type="warning" if run.phase == "failed" else "normal",
                reason=f"{kind} {run.phase}",
            )
        )
    return events


def _optional_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def _require_log_access(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> None:
    _require_inventory_access(db, current, workspace_id, cluster_id)
    if not _has_evidence_access(db, current, workspace_id, cluster_id):
        raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND)


def _require_inventory_access(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> None:
    try:
        require_cluster_access(
            db, current, workspace_id, cluster_id, Permission.INVENTORY_READ.value
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=404, detail=TARGET_NOT_FOUND) from exc
        raise


def _has_evidence_access(
    db: Any,
    current: Any,
    workspace_id: str,
    cluster_id: str,
) -> bool:
    try:
        require_cluster_access(
            db, current, workspace_id, cluster_id, Permission.EVIDENCE_READ.value
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


def _container_exists(resource: dict[str, Any], container: str | None) -> bool:
    if container is None:
        return True
    summary = resource.get("summary") if isinstance(resource.get("summary"), dict) else {}
    containers = summary.get("containers") if isinstance(summary.get("containers"), list) else []
    return any(
        isinstance(item, dict) and str(item.get("name") or "") == container for item in containers
    )


def _container_names(resource: dict[str, Any]) -> tuple[str, ...]:
    summary = resource.get("summary") if isinstance(resource.get("summary"), dict) else {}
    containers = summary.get("containers") if isinstance(summary.get("containers"), list) else []
    return tuple(
        sorted(
            {
                name
                for item in containers
                if isinstance(item, dict)
                and (name := str(item.get("name") or ""))
                and len(name) <= 63
                and KUBERNETES_CONTAINER_RE.fullmatch(name)
            }
        )
    )


def _safe_pod_regex(value: str) -> str:
    # Kubernetes DNS names are validated at the HTTP/inventory boundary. Dot is
    # the only regex metacharacter allowed by that alphabet.
    return value.replace(".", r"\.")


def _stream_label(stream: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = stream.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _observed_at(value: object) -> datetime | None:
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        try:
            nanoseconds = int(value)
            return datetime.fromtimestamp(nanoseconds / 1_000_000_000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.utcoffset() is not None else None
    return None


def _line_id(
    cluster_id: str,
    namespace: str,
    pod: str,
    container: str,
    timestamp_identity: str,
    line: str,
) -> str:
    canonical = "\x1f".join((cluster_id, namespace, pod, container, timestamp_identity, line))
    return f"log-{hashlib.sha256(canonical.encode()).hexdigest()[:32]}"


async def _is_disconnected(request: Request) -> bool:
    return bool(await request.is_disconnected())


async def _wait_or_disconnect(request: Request, seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if await _is_disconnected(request):
            return True
        await asyncio.sleep(
            min(LOG_STREAM_DISCONNECT_POLL_SECONDS, max(0.0, deadline - time.monotonic()))
        )
    return await _is_disconnected(request)
