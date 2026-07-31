"""command-worker 이벤트 body."""

from __future__ import annotations

from dataclasses import dataclass, field

from domains.gitops.events import Diff
from packages.contracts.event_bus.bodies.base import EventBody, JsonObject
from packages.contracts.event_bus.registry import event
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.gitops import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_DEPLOYMENT_BINDING_ID,
    DEFAULT_ENVIRONMENT,
    DEFAULT_WORKFLOW_RUN_ID,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID


@event(EventSubject.COMMAND_REQUESTED)
@dataclass(frozen=True)
class CommandRequestedBody(EventBody):
    """command.requested — 이 diff의 sandbox 적용 요청."""

    cluster_id: str
    action: str
    namespace: str
    reason: str
    diff: Diff
    # API 접수 UoW가 생성하는 안정적 trace ID. 과거 이벤트는 None으로 decode되어
    # worker의 기존 hash 기반 fallback을 유지한다.
    command_id: str | None = None
    workspace_id: str = DEFAULT_WORKSPACE_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    priority: int = 100
    requested_by: str | None = None
    actor: JsonObject | None = None
    approval_ref: str | None = None
    policy_decision_ref: str | None = None
    approval_decided_by: str | None = None
    approval_expires_at: str | None = None
    direct_execution: bool = False
    direct_execution_confirmed: bool = False
    payload: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class LeaseMetadata(EventBody):
    """명령 lease 유지 기준."""

    lease_seconds: int
    heartbeat_interval_seconds: int


@dataclass(frozen=True)
class RetryPolicy(EventBody):
    """agent 실행 실패 후 재처리 기준."""

    max_attempts: int
    retry_delay_seconds: int


@dataclass(frozen=True)
class RoutingConstraint(EventBody):
    """명령을 맡을 수 있는 target agent 조건."""

    channel: str
    cluster_id: str
    workspace_id: str
    required_capability: str


@dataclass(frozen=True)
class Plan(EventBody):
    """에이전트가 실행할 명령 계획(값 객체)."""

    command_id: str
    idempotency_key: str
    cluster_id: str
    action: str
    namespace: str
    diff: JsonObject
    payload: JsonObject
    steps: list[str]
    lease: LeaseMetadata
    retry_policy: RetryPolicy
    routing_constraint: RoutingConstraint
    workspace_id: str = DEFAULT_WORKSPACE_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    priority: int = 100
    approval_ref: str | None = None
    policy_decision_ref: str | None = None
    approval_decided_by: str | None = None
    approval_expires_at: str | None = None
    direct_execution: bool = False
    direct_execution_confirmed: bool = False


@dataclass(frozen=True)
class Route(EventBody):
    """명령을 보낼 경로(채널/클러스터, 값 객체)."""

    channel: str
    cluster_id: str


@event(EventSubject.COMMAND_DISPATCHED)
@dataclass(frozen=True)
class CommandDispatchedBody(EventBody):
    """command.dispatched — 정책 통과, 실행 계획 수립·대상 클러스터 라우팅.

    (구 command.dispatch.ready 를 흡수 — 계획 수립과 라우팅이 같은 핸들러에서
    동기적으로 일어나므로 단계를 나누지 않음.)
    """

    plan: Plan
    route: Route


@event(EventSubject.COMMAND_QUEUED_FOR_AGENT)
@dataclass(frozen=True)
class CommandQueuedForAgentBody(EventBody):
    """command.queued_for_agent — 에이전트 폴링 큐에 적재."""

    command_id: str
    cluster_id: str
    workspace_id: str = DEFAULT_WORKSPACE_ID
    application_id: str = DEFAULT_APPLICATION_ID
    workflow_run_id: str = DEFAULT_WORKFLOW_RUN_ID
    binding_id: str = DEFAULT_DEPLOYMENT_BINDING_ID
    environment: str = DEFAULT_ENVIRONMENT
    priority: int = 100
    approval_ref: str | None = None
    policy_decision_ref: str | None = None
    approval_decided_by: str | None = None
    approval_expires_at: str | None = None
    direct_execution: bool = False
    direct_execution_confirmed: bool = False


@event(EventSubject.COMMAND_REJECTED)
@dataclass(frozen=True)
class CommandRejectedBody(EventBody):
    """command.rejected — 정책 위반으로 거부(원요청 첨부)."""

    reason: str
    requested: JsonObject
    reason_code: str | None = None


@event(EventSubject.COMMAND_CANCEL_REQUESTED)
@dataclass(frozen=True)
class CommandCancelRequestedBody(EventBody):
    """Immutable, auditable cancel intent.  The repository owns its state transition."""

    command_id: str
    workspace_id: str
    reason: str | None = None
    requested_by: str | None = None
    actor: JsonObject | None = None


@event(EventSubject.COMMAND_RETRY_REQUESTED)
@dataclass(frozen=True)
class CommandRetryRequestedBody(EventBody):
    """Immutable, auditable manual retry intent for one failed logical command."""

    command_id: str
    workspace_id: str
    reason: str | None = None
    requested_by: str | None = None
    actor: JsonObject | None = None


@event(EventSubject.COMMAND_COMPLETED)
@dataclass(frozen=True)
class CommandCompletedBody(EventBody):
    """command.completed — 에이전트가 명령 실행 결과를 보고."""

    command_id: str
    result: JsonObject
