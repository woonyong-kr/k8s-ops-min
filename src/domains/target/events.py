"""target desired-state/reconciler 이벤트 계약."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.target import TargetReconcileStatus


@event(EventSubject.AGENT_CONNECTED)
@dataclass(frozen=True)
class AgentConnectedBody(EventBody):
    """agent.connected — target cluster agent가 management plane에 등록됨."""

    cluster_id: str
    agent_id: str
    capabilities: list[str] = field(default_factory=list)
    workspace_id: str = DEFAULT_WORKSPACE_ID


@dataclass(frozen=True)
class TargetDesiredComponent(EventBody):
    """Target cluster에 유지해야 하는 컴포넌트 목표 상태."""

    component: str
    namespace: str
    version: str
    spec: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class TargetDrift(EventBody):
    """desired state와 actual state 사이의 차이."""

    component: str
    reason: str
    desired: JsonObject
    actual: JsonObject | None = None


@event(EventSubject.CLUSTER_DESIRED_STATE_CHANGED)
@dataclass(frozen=True)
class ClusterDesiredStateChangedBody(EventBody):
    """cluster.desired_state.changed — target 목표 상태가 저장/변경됨."""

    cluster_id: str
    desired_state_version: str
    components: list[TargetDesiredComponent]
    reason: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    requested_by: str | None = None


@event(EventSubject.CLUSTER_RECONCILE_REQUESTED)
@dataclass(frozen=True)
class ClusterReconcileRequestedBody(EventBody):
    """cluster.reconcile.requested — target reconciler 실행 요청."""

    cluster_id: str
    desired_state_version: str
    reason: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    requested_by: str | None = None
    actual_state: JsonObject | None = None


@event(EventSubject.CLUSTER_RECONCILE_STARTED)
@dataclass(frozen=True)
class ClusterReconcileStartedBody(EventBody):
    """cluster.reconcile.started — desired/actual 비교 시작."""

    cluster_id: str
    desired_state_version: str
    component_count: int
    workspace_id: str = DEFAULT_WORKSPACE_ID


@event(EventSubject.CLUSTER_DRIFT_DETECTED)
@dataclass(frozen=True)
class ClusterDriftDetectedBody(EventBody):
    """cluster.drift.detected — 목표/실제 상태 차이 발견."""

    cluster_id: str
    desired_state_version: str
    drifts: list[TargetDrift]
    workspace_id: str = DEFAULT_WORKSPACE_ID


@event(EventSubject.CLUSTER_RECONCILE_COMPLETED)
@dataclass(frozen=True)
class ClusterReconcileCompletedBody(EventBody):
    """cluster.reconcile.completed — reconcile 판정 완료."""

    cluster_id: str
    desired_state_version: str
    status: str
    drifted: bool
    applied: bool
    message: str
    drifts: list[TargetDrift] = field(default_factory=list)
    workspace_id: str = DEFAULT_WORKSPACE_ID


@event(EventSubject.CLUSTER_RECONCILE_FAILED)
@dataclass(frozen=True)
class ClusterReconcileFailedBody(EventBody):
    """cluster.reconcile.failed — reconcile 중 처리 실패."""

    cluster_id: str
    desired_state_version: str
    error_type: str
    message: str
    status: str = TargetReconcileStatus.FAILED.value
    workspace_id: str = DEFAULT_WORKSPACE_ID


@event(EventSubject.EVIDENCE_JOB_UPDATED)
@dataclass(frozen=True)
class EvidenceJobUpdatedBody(EventBody):
    """agent evidence job 상태 변경 알림."""

    provider_key: str
    status: str
    evidence_key: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    cluster_id: str = ""
    application_id: str = ""
    evidence_emitted: bool = False
    source_id: str | None = None
    window_start: str | None = None
    workflow_run_id: str = ""
    correlation_id: str | None = None
    release_context: dict[str, Any] = field(default_factory=dict)
    collection_status: dict[str, Any] = field(default_factory=dict)


@event(EventSubject.EVIDENCE_JOBS_QUEUED)
@dataclass(frozen=True)
class EvidenceJobsQueuedBody(EventBody):
    """evidence job 큐잉 결과 반환."""

    workspace_id: str
    cluster_id: str
    evidence_key: str
    source_id: str
    window_start: str
    provider_keys: list[str]
    queued: int
    job_ids: list[str]
    workflow_run_id: str | None = None
    release_context: dict[str, Any] = field(default_factory=dict)
