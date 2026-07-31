"""command 도메인 HTTP 라우터 — 명령 발행 + agent 명령 풀(롱폴)·시작·결과."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Mapping
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from domains.command.actions import command_action_spec
from domains.command.debug_queries import (
    debug_query_plan,
    is_reserved_log_stream_query,
    queue_debug_query,
)
from domains.command.events import (
    CommandCancelRequestedBody,
    CommandRequestedBody,
    CommandRetryRequestedBody,
)
from domains.command.handler import build_plan
from domains.command.lifecycle import command_impact_identity
from domains.command.policy import (
    DEFAULT_COMMAND_LEASE_SECONDS,
)
from domains.command.repository import (
    CommandControlError,
    CommandControlNotFound,
    DuplicateCommandControl,
    LeasedAgentCommand,
    StartedAgentCommand,
    stage_command_control_in_transaction,
    stage_command_operation_event_in_transaction,
    stage_logical_command_acceptance_in_transaction,
)
from domains.command.scoped_metrics import (
    build_scoped_metric_plans,
    resolve_scoped_metric_identity,
)
from domains.gitops.events import Diff
from domains.identity.dependencies import (
    RESOURCE_ACCESS_DENIED_MESSAGE,
    ClusterAgentIdentity,
    require_cluster_access,
    require_cluster_agent,
    require_session,
)
from domains.inventory.action_catalog import resource_action_capability_id
from domains.inventory.capabilities import resource_capabilities_response
from domains.inventory.workload_revisions import workload_revision_selection
from domains.target.management_guard import (
    cluster_role_from_policy,
    is_management_registration,
    is_management_role,
    management_readonly_detail,
)
from domains.target.uninstall import UNINSTALL_CLEANUP_RESOURCE_REFS
from packages.config.constants import RCA_TEST_COMMAND_ACTIONS, Command, CommandStatus, Sandbox
from packages.config.control import (
    CONTROL_NAMESPACE_DENIED_MESSAGE,
    control_namespace_allowed,
)
from packages.config.settings import env
from packages.contracts.auth import Actor
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    AgentDebugQueryRequest,
    CommandControlRequest,
    CommandHeartbeatRequest,
    CommandRequest,
    CommandResultRequest,
    CommandStartRequest,
    ConfirmedResourceActionRequest,
    CronJobControlRequest,
    DeploymentRestartRequest,
    DeploymentScaleRequest,
    NodeDebugCleanupRequest,
    NodeDebugRequest,
    NodeDrainRequest,
    PodDebugRequest,
    WorkloadRollbackRequest,
)
from packages.contracts.gateway.responses import (
    AgentCommandPollResponse,
    AgentDebugQueryResponse,
    CommandHeartbeatResponse,
    CommandStartedResponse,
    CommandStatusResponse,
    EventIdAcceptedResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.parity import (
    CommandControlReceipt,
    CommandReceipt,
    OperationEvent,
    ResourceRef,
)
from packages.contracts.resource_files import (
    RESOURCE_FILE_ACTION,
    ResourceFileCommandPayload,
    ResourceFileCommandRequest,
)
from packages.contracts.scoped_metrics import (
    ScopedMetricCoverage,
    ScopedMetricQueryReceipt,
    ScopedMetricQueryRequest,
    ScopedMetricQueryResponse,
)
from packages.runtime.command_wakeup import WAKEUP
from packages.runtime.dependencies import get_db, get_events, get_operation_events
from packages.storage.retry import async_retry_db_conflict

# 롱폴 튜닝값 — env 미설정 시 기존 기본값과 동일한 기본값이 적용됨(배포 호환)
DEFAULT_POLL_SECONDS_ENV = "COMMAND_POLL_DEFAULT_SECONDS"  # 롱폴 기본 대기 초(기본 10)
DEFAULT_POLL_SECONDS = int(env(DEFAULT_POLL_SECONDS_ENV, "10"))
MAX_POLL_SECONDS_ENV = "COMMAND_POLL_MAX_SECONDS"  # 롱폴 최대 대기 초(기본 30)
MAX_POLL_SECONDS = int(env(MAX_POLL_SECONDS_ENV, "30"))
POLL_SLEEP_SECONDS_ENV = "COMMAND_POLL_SLEEP_SECONDS"  # 롱폴 반복 간 대기 초(기본 1)
POLL_SLEEP_SECONDS = int(env(POLL_SLEEP_SECONDS_ENV, "1"))
LEASE_SECONDS = DEFAULT_COMMAND_LEASE_SECONDS
NOT_FOUND_CODE = 404
NOT_FOUND_MESSAGE = "command not found"
RESOURCE_ACCESS_DENIED = RESOURCE_ACCESS_DENIED_MESSAGE
# 수동 명령도 대상(diff)은 클라이언트가 명시해야 함 — 서버가 임의 리소스를 합성하지 않음.
UNPROCESSABLE_CODE = 422
MANUAL_DIFF_REQUIRED_MESSAGE = "diff is required for manual command requests"
DIRECT_EXECUTION_CONFIRMATION_REQUIRED_MESSAGE = "direct command requires explicit confirmation"
DEDICATED_COMMAND_ACTION_REQUIRED_MESSAGE = (
    "this action requires its typed dedicated command endpoint"
)
RCA_TEST_ACTION_DEDICATED_API_REQUIRED = (
    "RCA test actions are reserved; use the dedicated /rca/test-runs API"
)
# 제어 허용 네임스페이스는 packages.config.control 단일 기준(기본 sandbox 만).
CONTROL_NAMESPACE_NOT_ALLOWED = CONTROL_NAMESPACE_DENIED_MESSAGE
COMMAND_PRIORITY_HIGH = 100
RESERVED_LOG_STREAM_QUERY_MESSAGE = "reserved browser log stream query"
OPERATION_EVENT_REPLAY_POLL_SECONDS = 5.0
CRONJOB_RESOURCE_STALE = "selected CronJob capability is stale"
CRONJOB_IDEMPOTENCY_REUSED = "cronjob_idempotency_key_reused"
WORKLOAD_ROLLBACK_STALE = "workload_rollback_stale"
WORKLOAD_ROLLBACK_IDEMPOTENCY_REUSED = "workload_rollback_idempotency_key_reused"
WORKLOAD_RESTART_ACTIONS = {
    "deployment": Command.DEFAULT_ACTION,
    "statefulset": Command.KUBERNETES_STATEFULSET_RESTART_ACTION,
    "daemonset": Command.KUBERNETES_DAEMONSET_RESTART_ACTION,
}
WORKLOAD_SCALE_ACTIONS = {
    "deployment": Command.KUBERNETES_DEPLOYMENT_SCALE_ACTION,
    "statefulset": Command.KUBERNETES_STATEFULSET_SCALE_ACTION,
}
WORKLOAD_ROLLBACK_ACTIONS = {
    "deployment": Command.KUBERNETES_DEPLOYMENT_ROLLBACK_ACTION,
    "statefulset": Command.KUBERNETES_STATEFULSET_ROLLBACK_ACTION,
    "daemonset": Command.KUBERNETES_DAEMONSET_ROLLBACK_ACTION,
}

# `/commands` carries only an inspected diff. Actions that need a typed target
# payload (replicas, Helm values, uninstall contract, and similar) must use their
# dedicated endpoint so a queued receipt is always executable by the agent.
MANUAL_DIFF_ACTIONS = frozenset({Command.DEFAULT_ACTION, Command.APPLY_MANIFEST_ACTION})
MAX_ACTIVE_RESOURCE_FILE_COMMANDS = 32

router = APIRouter()
__all__ = ["debug_query_plan", "router"]


def new_command_id() -> str:
    """Create a server-owned command trace before the acceptance UoW starts."""
    return f"cmd-{uuid4()}"


def command_accepted_response(command: CommandRequestedBody, accepted: Any) -> CommandReceipt:
    command_id = command.command_id or build_plan(command, accepted.event.correlation_id).command_id
    event_id = str(accepted.event.event_id)
    return CommandReceipt(
        accepted=True,
        command_id=command_id,
        event_id=event_id,
        # audit worker가 비동기로 audit_log.event_id에 투영하는 immutable source ID.
        # audit_log.id는 이 시점에 존재하지 않을 수 있어 의도적으로 노출하지 않는다.
        audit_event_id=event_id,
        correlation_id=accepted.event.correlation_id,
        status=CommandStatus.QUEUED,
    )


async def accept_command_with_receipt_stage(
    events: Any,
    command: CommandRequestedBody,
    *,
    actor: Actor,
    max_active_per_action: int | None = None,
) -> tuple[Any, OperationEvent | None]:
    """Accept a command and stage its browser receipt in the same event outbox UoW."""
    staged: list[OperationEvent | None] = []

    def stage(conn: Any, accepted_event: Any) -> None:
        plan = build_plan(command, accepted_event.correlation_id)
        stage_logical_command_acceptance_in_transaction(
            conn,
            correlation_id=accepted_event.correlation_id,
            plan=plan.to_body(),
            confirmation_event_id=str(accepted_event.event_id),
            max_active_per_action=max_active_per_action,
        )
        staged.append(
            stage_command_operation_event_in_transaction(
                conn,
                workspace_id=command.workspace_id,
                command_id=plan.command_id,
                kind="progress",
                payload={
                    "cluster_id": command.cluster_id,
                    "status": CommandStatus.QUEUED,
                    "action": command.action,
                    "correlation_id": accepted_event.correlation_id,
                },
            )
        )

    accepted = await events.accept_body(command, actor=actor, transactional_stage=stage)
    return accepted, staged[0] if staged else None


async def publish_operation_event(
    operation_events: Any,
    *,
    command_id: str | None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    status: str,
    payload: dict[str, object],
) -> None:
    """Publish through the injected cross-replica broker when an HTTP route provides it."""
    if not command_id:
        return
    publish = getattr(operation_events, "publish", None)
    if not callable(publish):
        return
    kind = (
        "completed"
        if status == CommandStatus.COMPLETED
        else "failed"
        if status == CommandStatus.FAILED
        else "cancelled"
        if status == CommandStatus.CANCELLED
        else "progress"
    )
    await publish(
        command_id=command_id,
        kind=kind,
        payload={"status": status, **payload},
        workspace_id=workspace_id,
    )


async def announce_staged_operation_event(
    operation_events: Any,
    event: OperationEvent | None,
    *,
    workspace_id: str,
) -> bool:
    """Fan out an event already committed by a lifecycle transaction exactly once."""
    if event is None:
        return False
    announce = getattr(operation_events, "announce", None)
    if not callable(announce):
        return False
    await announce(event, workspace_id=workspace_id)
    return True


async def publish_accepted_operation(
    operation_events: Any,
    command: CommandRequestedBody,
    response: CommandReceipt,
) -> None:
    await publish_operation_event(
        operation_events,
        command_id=response.command_id,
        workspace_id=command.workspace_id,
        status=CommandStatus.QUEUED,
        payload={
            "cluster_id": command.cluster_id,
            "action": command.action,
            "correlation_id": response.correlation_id,
        },
    )


def command_snapshot_event(row: dict[str, object]) -> OperationEvent:
    status = str(row["status"])
    kind = (
        "completed"
        if status == CommandStatus.COMPLETED
        else "failed"
        if status == CommandStatus.FAILED
        else "cancelled"
        if status == CommandStatus.CANCELLED
        else "progress"
    )
    return OperationEvent(
        command_id=str(row["command_id"]),
        sequence=1,
        kind=kind,
        payload={
            "status": status,
            "cluster_id": str(row["cluster_id"]),
            "action": str(row["action"]),
            "correlation_id": str(row["correlation_id"]),
            "result": dict(row.get("result") or {}),
        },
    )


def sse_operation_event(event: OperationEvent) -> str:
    return f"id: {event.sequence}\nevent: operation\ndata: {event.model_dump_json()}\n\n"


def operation_event_cursor(after: int | None, last_event_id: str | None) -> int:
    """Resolve the equivalent query/header SSE cursors without ambiguity."""
    if last_event_id is None or not last_event_id.strip():
        return after or 0
    try:
        cursor = int(last_event_id)
    except ValueError as exc:
        raise HTTPException(status_code=UNPROCESSABLE_CODE, detail="invalid Last-Event-ID") from exc
    if cursor < 0:
        raise HTTPException(status_code=UNPROCESSABLE_CODE, detail="invalid Last-Event-ID")
    if after is not None and after != cursor:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE, detail="conflicting operation event cursor"
        )
    return cursor


def command_diff(payload: CommandRequest, workspace_id: str) -> Diff:
    if not payload.diff:
        raise HTTPException(status_code=UNPROCESSABLE_CODE, detail=MANUAL_DIFF_REQUIRED_MESSAGE)
    raw = {**payload.diff, "workspace_id": workspace_id, "cluster_id": payload.cluster_id}
    return cast(Diff, Diff.from_body(raw))


def require_direct_execution_confirmation(payload: Any) -> None:
    """Reject legacy mode flags unless a real one-time confirmation is present.

    ``direct_execution`` is no longer a client-controlled execution grant.  The
    server derives that mode solely from the common ``confirmation: true`` field
    after RBAC, target, and diff validation finish in the route.
    """
    legacy_requested = bool(
        getattr(payload, "direct_execution", False)
        or getattr(payload, "direct_execution_confirmed", False)
    )
    if legacy_requested and getattr(payload, "confirmation", None) is not True:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=DIRECT_EXECUTION_CONFIRMATION_REQUIRED_MESSAGE,
        )


def direct_execution_from_confirmation(payload: Any) -> bool:
    """The only server-side rule that enables immediate agent dispatch."""
    return getattr(payload, "confirmation", None) is True


def validate_control_namespace(namespace: str) -> None:
    if not control_namespace_allowed(namespace):
        raise HTTPException(status_code=UNPROCESSABLE_CODE, detail=CONTROL_NAMESPACE_NOT_ALLOWED)


def require_cluster_deploy_access(
    db: Any, current: Any, workspace_id: str, cluster_id: str
) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.DEPLOY_RUN.value,
        detail=RESOURCE_ACCESS_DENIED,
    )


def require_not_management_cluster(
    db: Any, workspace_id: str, cluster_id: str, *, direct_execution: bool = False
) -> None:
    if direct_execution:
        return
    registration_getter = getattr(db, "get_cluster_registration", None)
    registration = (
        registration_getter(workspace_id, cluster_id) if callable(registration_getter) else None
    )
    policy_getter = getattr(db, "get_cluster_policy", None)
    policy = policy_getter(workspace_id, cluster_id) if callable(policy_getter) else None
    if is_management_registration(registration) or is_management_role(
        cluster_role_from_policy(policy)
    ):
        raise HTTPException(status_code=400, detail=management_readonly_detail())


def resource_control_diff(
    *,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
    resource_kind: str,
    resource_name: str,
    action: str,
    basis: JsonObject,
) -> Diff:
    return Diff(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource=f"{resource_kind}/{resource_name}",
        namespace=namespace,
        desired_image="",
        actual_image="resource-not-inspected",
        risk=Sandbox.RISK_TAG,
        status=action,
        basis=basis,
    )


async def accept_resource_control(
    *,
    cluster_id: str,
    namespace: str | None,
    resource_kind: str,
    resource_name: str,
    action: str,
    reason: str,
    payload: JsonObject,
    approval_ref: str | None,
    policy_decision_ref: str | None,
    execution_request: Any,
    operation_events: Any,
    current: Any,
    db: Any,
    events: Any,
    command_id: str | None = None,
    diff_basis: JsonObject | None = None,
) -> CommandReceipt:
    if namespace is not None:
        validate_control_namespace(namespace)
    require_direct_execution_confirmation(execution_request)
    direct_execution = direct_execution_from_confirmation(execution_request)
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_deploy_access(db, current, workspace_id, cluster_id)
    require_not_management_cluster(db, workspace_id, cluster_id, direct_execution=direct_execution)
    diff = resource_control_diff(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace or "",
        resource_kind=resource_kind,
        resource_name=resource_name,
        action=action,
        basis=diff_basis if diff_basis is not None else payload,
    )
    command = CommandRequestedBody(
        cluster_id=cluster_id,
        action=action,
        namespace=namespace or "",
        reason=reason,
        diff=diff,
        command_id=command_id or new_command_id(),
        payload=payload,
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=current.user_id,
        approval_ref=approval_ref,
        policy_decision_ref=policy_decision_ref,
        direct_execution=direct_execution,
        direct_execution_confirmed=direct_execution,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(current.user_id, tuple(current.roles)),
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events, receipt_event, workspace_id=command.workspace_id
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


async def accept_node_control(
    *,
    cluster_id: str,
    node: str,
    action: str,
    reason: str,
    unschedulable: bool,
    payload: ConfirmedResourceActionRequest,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandReceipt:
    if payload.confirmation is not True:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=DIRECT_EXECUTION_CONFIRMATION_REQUIRED_MESSAGE,
        )
    return await accept_resource_control(
        cluster_id=cluster_id,
        namespace=None,
        resource_kind="node",
        resource_name=node,
        action=action,
        reason=payload.reason or reason,
        payload={"name": node, "unschedulable": unschedulable},
        approval_ref=None,
        policy_decision_ref=None,
        execution_request=payload,
        operation_events=operation_events,
        current=current,
        db=db,
        events=events,
    )


def require_cluster_read_access(db: Any, current: Any, workspace_id: str, cluster_id: str) -> None:
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.EVIDENCE_READ.value,
        detail=RESOURCE_ACCESS_DENIED,
    )


async def lease_next_command(
    db: Any, cluster_id: str, workspace_id: str, agent_id: str, timeout: int
) -> Any | None:
    """롱폴 — 이 클러스터의 다음 명령을 timeout 까지 대기하며 리스(아웃바운드 단일 채널).

    대기는 LISTEN/NOTIFY 웨이크업(WAKEUP)을 우선 사용 — 명령 큐잉 순간 즉시
    재시도한다. 리스너 미가동이면 wait 가 타임아웃까지 잠들어 기존 주기 폴링과
    동일하게 동작한다(정확성은 폴링이, 지연·부하 개선은 알림이 담당).
    """
    deadline = time.time() + min(timeout, MAX_POLL_SECONDS)
    while time.time() < deadline:
        row = await async_retry_db_conflict(
            lambda: db.lease_agent_command(
                cluster_id,
                workspace_id,
                CommandStatus.QUEUED,
                CommandStatus.LEASED,
                agent_id,
                LEASE_SECONDS,
            )
        )
        if row:
            return row
        await WAKEUP.wait(workspace_id, cluster_id, min(POLL_SLEEP_SECONDS, deadline - time.time()))
    return None


@router.post(
    gateway_routes.COMMANDS_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def commands(
    payload: CommandRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    if payload.action in RCA_TEST_COMMAND_ACTIONS:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=RCA_TEST_ACTION_DEDICATED_API_REQUIRED,
        )
    if payload.action not in MANUAL_DIFF_ACTIONS:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=DEDICATED_COMMAND_ACTION_REQUIRED_MESSAGE,
        )
    require_direct_execution_confirmation(payload)
    direct_execution = direct_execution_from_confirmation(payload)
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_deploy_access(db, current, workspace_id, payload.cluster_id)
    require_not_management_cluster(
        db, workspace_id, payload.cluster_id, direct_execution=direct_execution
    )
    command = CommandRequestedBody(
        cluster_id=payload.cluster_id,
        action=payload.action,
        namespace=payload.namespace,
        reason=payload.reason or "manual command request",
        diff=command_diff(payload, workspace_id),
        command_id=new_command_id(),
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=current.user_id,
        approval_ref=payload.approval_ref,
        policy_decision_ref=payload.policy_decision_ref,
        direct_execution=direct_execution,
        direct_execution_confirmed=direct_execution,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(current.user_id, tuple(current.roles)),
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events, receipt_event, workspace_id=command.workspace_id
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


@router.post(
    gateway_routes.RESOURCE_FILE_COMMAND_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def read_resource_file(
    payload: ResourceFileCommandRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    """Queue one exact bounded filesystem read for the outbound cluster Agent."""

    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    resource = payload.resource
    if (
        resource.api_group != ""
        or resource.version != "v1"
        or resource.kind.casefold() != "pod"
        or resource.namespace is None
    ):
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE, detail="resource file target must be a Pod"
        )
    inventory_resource = _resource_file_inventory(db, workspace_id, payload.resource_id)
    cluster_id = str(inventory_resource.get("cluster_id") or "")
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.INVENTORY_READ.value,
        detail=RESOURCE_ACCESS_DENIED,
    )
    inventory_resource, current_resource = exact_action_capability_resource(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=resource.namespace,
        resource_kind="pod",
        resource_name=resource.name,
        capability_id=payload.capability_id,
        payload=payload,
        inventory_resource=inventory_resource,
    )
    resource_version = str(inventory_resource.get("resource_version") or "")
    if not resource_version:
        raise HTTPException(status_code=409, detail="resource file target is stale")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "workspace_id": workspace_id,
                "user_id": str(current.user_id),
                **payload.model_dump(mode="json", exclude={"confirmation", "idempotency_key"}),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    command_id = resource_action_command_id(
        workspace_id, str(current.user_id), payload.idempotency_key
    )
    replay = await replay_resource_action_receipt(
        db,
        workspace_id=workspace_id,
        command_id=command_id,
        request_fingerprint=fingerprint,
        idempotency_reused_code="resource_file_idempotency_key_reused",
    )
    if replay is not None:
        return replay
    agent_payload = ResourceFileCommandPayload(
        **payload.model_dump(exclude={"confirmation", "idempotency_key"}),
        pod_resource_version=resource_version,
    )
    diff = Diff(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource=f"Pod/{resource.name}",
        namespace=resource.namespace,
        desired_image="",
        actual_image="resource-observed",
        risk=Sandbox.RISK_TAG,
        status="resource_files_read",
        has_changes=False,
        changes=[
            {
                "operation": payload.operation,
                "path": payload.path,
                "container": payload.container,
            }
        ],
        basis={
            "resource_id": payload.resource_id,
            "snapshot_id": payload.snapshot_id,
            "capability_revision": payload.capability_revision,
            "capability_id": payload.capability_id,
            "resource_ref": current_resource.model_dump(),
            "request_fingerprint": fingerprint,
        },
    )
    command = CommandRequestedBody(
        cluster_id=cluster_id,
        action=RESOURCE_FILE_ACTION,
        namespace=resource.namespace,
        reason="operator confirmed a bounded resource filesystem read",
        diff=diff,
        command_id=command_id,
        payload=agent_payload.model_dump(mode="json"),
        workspace_id=workspace_id,
        priority=COMMAND_PRIORITY_HIGH,
        requested_by=current.user_id,
        direct_execution=True,
        direct_execution_confirmed=True,
    )
    accepted, receipt_event = await accept_command_with_receipt_stage(
        events,
        command,
        actor=Actor(current.user_id, tuple(current.roles)),
        max_active_per_action=MAX_ACTIVE_RESOURCE_FILE_COMMANDS,
    )
    response = command_accepted_response(command, accepted)
    if not await announce_staged_operation_event(
        operation_events, receipt_event, workspace_id=workspace_id
    ):
        await publish_accepted_operation(operation_events, command, response)
    return response


def _resource_file_inventory(db: Any, workspace_id: str, resource_id: str) -> dict[str, Any]:
    reader = getattr(db, "get_inventory_resource_by_key", None)
    resource = (
        reader(workspace_id=workspace_id, inventory_key=resource_id) if callable(reader) else None
    )
    if not isinstance(resource, dict):
        raise HTTPException(status_code=404, detail="inventory resource not found")
    if not str(resource.get("cluster_id") or ""):
        raise HTTPException(status_code=409, detail="resource file target is stale")
    return resource


def workload_action(actions: dict[str, str], kind: str) -> tuple[str, str]:
    normalized_kind = kind.strip().casefold()
    action = actions.get(normalized_kind)
    if action is None:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=f"unsupported workload kind: {kind}",
        )
    return normalized_kind, action


async def accept_workload_scale(
    *,
    cluster_id: str,
    namespace: str,
    kind: str,
    workload: str,
    payload: DeploymentScaleRequest,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandReceipt:
    resource_kind, action = workload_action(WORKLOAD_SCALE_ACTIONS, kind)
    command_payload = {
        "namespace": namespace,
        "name": workload,
        "replicas": payload.replicas,
    }
    return await accept_resource_control(
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind=resource_kind,
        resource_name=workload,
        action=action,
        reason=payload.reason or f"scale {resource_kind}/{workload}",
        payload=command_payload,
        approval_ref=payload.approval_ref,
        policy_decision_ref=payload.policy_decision_ref,
        execution_request=payload,
        operation_events=operation_events,
        current=current,
        db=db,
        events=events,
    )


async def accept_workload_restart(
    *,
    cluster_id: str,
    namespace: str,
    kind: str,
    workload: str,
    payload: DeploymentRestartRequest,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandReceipt:
    resource_kind, action = workload_action(WORKLOAD_RESTART_ACTIONS, kind)
    return await accept_resource_control(
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind=resource_kind,
        resource_name=workload,
        action=action,
        reason=payload.reason or f"restart {resource_kind}/{workload}",
        payload={"namespace": namespace, "name": workload},
        approval_ref=payload.approval_ref,
        policy_decision_ref=payload.policy_decision_ref,
        execution_request=payload,
        operation_events=operation_events,
        current=current,
        db=db,
        events=events,
    )


@router.post(
    gateway_routes.CLUSTER_DEPLOYMENT_SCALE_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def scale_deployment(
    cluster_id: str,
    namespace: str,
    deployment: str,
    payload: DeploymentScaleRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_workload_scale(
        cluster_id=cluster_id,
        namespace=namespace,
        kind="deployment",
        workload=deployment,
        payload=payload,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_WORKLOAD_SCALE_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def scale_workload(
    cluster_id: str,
    namespace: str,
    kind: str,
    workload: str,
    payload: DeploymentScaleRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_workload_scale(
        cluster_id=cluster_id,
        namespace=namespace,
        kind=kind,
        workload=workload,
        payload=payload,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_DEPLOYMENT_RESTART_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def restart_deployment(
    cluster_id: str,
    namespace: str,
    deployment: str,
    payload: DeploymentRestartRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_workload_restart(
        cluster_id=cluster_id,
        namespace=namespace,
        kind="deployment",
        workload=deployment,
        payload=payload,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_WORKLOAD_RESTART_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def restart_workload(
    cluster_id: str,
    namespace: str,
    kind: str,
    workload: str,
    payload: DeploymentRestartRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_workload_restart(
        cluster_id=cluster_id,
        namespace=namespace,
        kind=kind,
        workload=workload,
        payload=payload,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_NODE_CORDON_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def cordon_node(
    cluster_id: str,
    node: str,
    payload: ConfirmedResourceActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_node_control(
        cluster_id=cluster_id,
        node=node,
        action=Command.KUBERNETES_NODE_CORDON_ACTION,
        reason=f"cordon node/{node}",
        unschedulable=True,
        payload=payload,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_NODE_UNCORDON_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def uncordon_node(
    cluster_id: str,
    node: str,
    payload: ConfirmedResourceActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_node_control(
        cluster_id=cluster_id,
        node=node,
        action=Command.KUBERNETES_NODE_UNCORDON_ACTION,
        reason=f"uncordon node/{node}",
        unschedulable=False,
        payload=payload,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_NODE_DRAIN_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def drain_node(
    cluster_id: str,
    node: str,
    payload: NodeDrainRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_exact_resource_action(
        cluster_id=cluster_id,
        namespace=None,
        resource_kind="node",
        resource_name=node,
        capability_id="node.drain",
        action=Command.KUBERNETES_NODE_DRAIN_ACTION,
        reason=payload.reason or f"drain node/{node}",
        payload=payload,
        idempotency_key=idempotency_key,
        command_payload={
            "timeout_seconds": payload.timeout_seconds,
            "max_parallel": payload.max_parallel,
            "max_pods": payload.max_pods,
            "force": payload.force,
            "delete_empty_dir_data": payload.delete_empty_dir_data,
        },
        resource_ref_key="node_ref",
        resource_version_key="node_resource_version",
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_POD_DEBUG_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def debug_pod(
    cluster_id: str,
    namespace: str,
    pod: str,
    payload: PodDebugRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    suffix = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
    return await accept_exact_resource_action(
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind="pod",
        resource_name=pod,
        capability_id="pod.debug",
        action=Command.KUBERNETES_POD_DEBUG_ACTION,
        reason=payload.reason or f"debug pod/{namespace}/{pod}",
        payload=payload,
        idempotency_key=idempotency_key,
        command_payload={
            "target_container": payload.target_container,
            "container_name": f"opsia-debug-{suffix}",
            "image": payload.image,
        },
        resource_ref_key="pod_ref",
        resource_version_key="pod_resource_version",
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_NODE_DEBUG_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def debug_node(
    cluster_id: str,
    node: str,
    payload: NodeDebugRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    validate_control_namespace(payload.namespace)
    suffix = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
    session_id = f"debug-session-{suffix}"
    return await accept_exact_resource_action(
        cluster_id=cluster_id,
        namespace=None,
        resource_kind="node",
        resource_name=node,
        capability_id="node.debug",
        action=Command.KUBERNETES_NODE_DEBUG_ACTION,
        reason=payload.reason or f"debug node/{node}",
        payload=payload,
        idempotency_key=idempotency_key,
        command_payload={
            "namespace": payload.namespace,
            "session_id": session_id,
            "debug_pod_name": f"opsia-node-debug-{suffix}",
            "image": payload.image,
        },
        resource_ref_key="node_ref",
        resource_version_key="node_resource_version",
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_NODE_DEBUG_CLEANUP_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def cleanup_node_debug(
    cluster_id: str,
    node: str,
    payload: NodeDebugCleanupRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    validate_control_namespace(payload.namespace)
    session_suffix = payload.session_id.removeprefix("debug-session-")
    if not session_suffix or len(session_suffix) != 16:
        raise HTTPException(status_code=422, detail="debug session identity is invalid")
    return await accept_exact_resource_action(
        cluster_id=cluster_id,
        namespace=None,
        resource_kind="node",
        resource_name=node,
        capability_id="node.debug.cleanup",
        action=Command.KUBERNETES_NODE_DEBUG_CLEANUP_ACTION,
        reason=payload.reason or f"cleanup debug node/{node}",
        payload=payload,
        idempotency_key=idempotency_key,
        command_payload={
            "namespace": payload.namespace,
            "session_id": payload.session_id,
            "debug_pod_name": f"opsia-node-debug-{session_suffix}",
        },
        resource_ref_key="node_ref",
        resource_version_key="node_resource_version",
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


async def accept_exact_resource_action(
    *,
    cluster_id: str,
    namespace: str | None,
    resource_kind: str,
    resource_name: str,
    capability_id: str,
    action: str,
    reason: str,
    payload: NodeDrainRequest | PodDebugRequest | NodeDebugRequest | NodeDebugCleanupRequest,
    idempotency_key: str,
    command_payload: JsonObject,
    resource_ref_key: str,
    resource_version_key: str,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandReceipt:
    if payload.confirmation is not True:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=DIRECT_EXECUTION_CONFIRMATION_REQUIRED_MESSAGE,
        )
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    inventory_resource, current_resource = exact_action_capability_resource(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind=resource_kind,
        resource_name=resource_name,
        capability_id=capability_id,
        payload=payload,
    )
    resource_version = str(inventory_resource.get("resource_version") or "")
    if not resource_version:
        raise HTTPException(status_code=409, detail="resource action identity is stale")
    fingerprint = exact_action_request_fingerprint(
        workspace_id=workspace_id,
        user_id=str(current.user_id),
        action=action,
        reason=reason,
        payload=payload,
        command_payload=command_payload,
    )
    command_id = resource_action_command_id(workspace_id, str(current.user_id), idempotency_key)
    replay = await replay_resource_action_receipt(
        db,
        workspace_id=workspace_id,
        command_id=command_id,
        request_fingerprint=fingerprint,
    )
    if replay is not None:
        return replay
    execution_payload = {
        "name": resource_name,
        resource_ref_key: current_resource.model_dump(),
        resource_version_key: resource_version,
    }
    if namespace is not None:
        execution_payload["namespace"] = namespace
    execution_payload.update(command_payload)
    return await accept_resource_control(
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind=resource_kind,
        resource_name=resource_name,
        action=action,
        reason=reason,
        payload=execution_payload,
        approval_ref=None,
        policy_decision_ref=None,
        execution_request=payload,
        operation_events=operation_events,
        current=current,
        db=db,
        events=events,
        command_id=command_id,
        diff_basis={
            "resource_id": payload.resource_id,
            "capability_snapshot_id": payload.snapshot_id,
            "capability_revision": payload.capability_revision,
            "capability_id": capability_id,
            "resource_ref": current_resource.model_dump(),
            "inventory_resource_version": resource_version,
            "request_fingerprint": fingerprint,
            "options": command_payload,
        },
    )


def exact_action_capability_resource(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    namespace: str | None,
    resource_kind: str,
    resource_name: str,
    capability_id: str,
    payload: NodeDrainRequest
    | PodDebugRequest
    | NodeDebugRequest
    | NodeDebugCleanupRequest
    | ResourceFileCommandRequest,
    inventory_resource: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ResourceRef]:
    reader = getattr(db, "get_inventory_resource_by_key", None)
    resource = inventory_resource
    if resource is None:
        resource = (
            reader(workspace_id=workspace_id, inventory_key=payload.resource_id)
            if callable(reader)
            else None
        )
    if not isinstance(resource, dict):
        raise HTTPException(status_code=409, detail="resource action identity is stale")
    current_resource = inventory_resource_ref(resource)
    if (
        str(resource.get("snapshot_id") or "") != payload.snapshot_id
        or current_resource != payload.resource
        or current_resource.kind.casefold() != resource_kind.casefold()
        or current_resource.namespace != namespace
        or current_resource.name != resource_name
        or str(resource.get("cluster_id") or "") != cluster_id
    ):
        raise HTTPException(status_code=409, detail="resource action identity is stale")
    decision = resource_capabilities_response(
        db,
        workspace_id=workspace_id,
        current=current,
        resource=resource,
    )
    if decision.revision != payload.capability_revision or capability_id not in {
        item.capability_id for item in decision.capabilities
    }:
        raise HTTPException(status_code=409, detail="resource action capability is stale")
    return resource, current_resource


def exact_action_request_fingerprint(
    *,
    workspace_id: str,
    user_id: str,
    action: str,
    reason: str,
    payload: NodeDrainRequest | PodDebugRequest | NodeDebugRequest | NodeDebugCleanupRequest,
    command_payload: JsonObject,
) -> str:
    encoded = json.dumps(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "action": action,
            "reason": reason,
            "resource_id": payload.resource_id,
            "snapshot_id": payload.snapshot_id,
            "capability_revision": payload.capability_revision,
            "resource": payload.resource.model_dump(),
            "options": command_payload,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


async def accept_cronjob_control(
    *,
    cluster_id: str,
    namespace: str,
    cronjob: str,
    action: str,
    reason: str,
    payload: CronJobControlRequest,
    idempotency_key: str,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandReceipt:
    if payload.confirmation is not True:
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=DIRECT_EXECUTION_CONFIRMATION_REQUIRED_MESSAGE,
        )
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    capability_id = resource_action_capability_id(action)
    if capability_id is None:
        raise HTTPException(status_code=409, detail=CRONJOB_RESOURCE_STALE)
    inventory_resource, current_resource = exact_cronjob_capability_resource(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        cronjob=cronjob,
        capability_id=capability_id,
        payload=payload,
    )
    request_fingerprint = cronjob_request_fingerprint(
        workspace_id=workspace_id,
        user_id=str(current.user_id),
        action=action,
        reason=payload.reason or reason,
        payload=payload,
    )
    command_id = resource_action_command_id(
        workspace_id,
        str(current.user_id),
        idempotency_key,
    )
    replay = await replay_resource_action_receipt(
        db,
        workspace_id=workspace_id,
        command_id=command_id,
        request_fingerprint=request_fingerprint,
    )
    if replay is not None:
        return replay
    diff_basis = {
        "resource_id": payload.resource_id,
        "capability_snapshot_id": payload.snapshot_id,
        "capability_revision": payload.capability_revision,
        "capability_id": capability_id,
        "resource_ref": current_resource.model_dump(),
        "inventory_resource_version": str(inventory_resource.get("resource_version") or ""),
        "request_fingerprint": request_fingerprint,
    }
    return await accept_resource_control(
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind="cronjob",
        resource_name=cronjob,
        action=action,
        reason=payload.reason or reason,
        payload={
            "namespace": namespace,
            "name": cronjob,
            "resource_ref": current_resource.model_dump(),
        },
        approval_ref=None,
        policy_decision_ref=None,
        execution_request=payload,
        operation_events=operation_events,
        current=current,
        db=db,
        events=events,
        command_id=command_id,
        diff_basis=diff_basis,
    )


def exact_cronjob_capability_resource(
    db: Any,
    *,
    current: Any,
    workspace_id: str,
    cluster_id: str,
    namespace: str,
    cronjob: str,
    capability_id: str,
    payload: CronJobControlRequest,
) -> tuple[dict[str, Any], ResourceRef]:
    reader = getattr(db, "get_inventory_resource_by_key", None)
    if not callable(reader):
        raise HTTPException(status_code=409, detail=CRONJOB_RESOURCE_STALE)
    resource = reader(workspace_id=workspace_id, inventory_key=payload.resource_id)
    if not isinstance(resource, dict):
        raise HTTPException(status_code=409, detail=CRONJOB_RESOURCE_STALE)
    current_resource = inventory_resource_ref(resource)
    if (
        str(resource.get("snapshot_id") or "") != payload.snapshot_id
        or current_resource != payload.resource
        or current_resource.api_group != "batch"
        or current_resource.version != "v1"
        or current_resource.kind.casefold() != "cronjob"
        or current_resource.namespace != namespace
        or current_resource.name != cronjob
        or str(resource.get("cluster_id") or "") != cluster_id
    ):
        raise HTTPException(status_code=409, detail=CRONJOB_RESOURCE_STALE)
    decision = resource_capabilities_response(
        db,
        workspace_id=workspace_id,
        current=current,
        resource=resource,
    )
    enabled = {item.capability_id for item in decision.capabilities}
    if decision.revision != payload.capability_revision or capability_id not in enabled:
        raise HTTPException(status_code=409, detail=CRONJOB_RESOURCE_STALE)
    return resource, current_resource


def inventory_resource_ref(resource: Mapping[str, object]) -> ResourceRef:
    api_version = str(resource.get("api_version") or "").strip().strip("/")
    segments = api_version.split("/") if api_version else []
    if len(segments) == 1:
        api_group, version = "", segments[0]
    elif len(segments) == 2:
        api_group, version = segments
    else:
        raise HTTPException(status_code=409, detail=CRONJOB_RESOURCE_STALE)
    uid = str(resource.get("uid") or "")
    namespace = resource.get("namespace")
    try:
        return ResourceRef(
            api_group=api_group,
            version=version,
            kind=str(resource.get("kind") or ""),
            namespace=str(namespace) if namespace is not None else None,
            name=str(resource.get("name") or ""),
            uid=uid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=CRONJOB_RESOURCE_STALE) from exc


def cronjob_request_fingerprint(
    *,
    workspace_id: str,
    user_id: str,
    action: str,
    reason: str,
    payload: CronJobControlRequest,
) -> str:
    encoded = json.dumps(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "action": action,
            "reason": reason,
            "resource_id": payload.resource_id,
            "snapshot_id": payload.snapshot_id,
            "capability_revision": payload.capability_revision,
            "resource": payload.resource.model_dump(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def resource_action_command_id(workspace_id: str, user_id: str, idempotency_key: str) -> str:
    authority = "\0".join((workspace_id, user_id, idempotency_key))
    return f"cmd-resource-{hashlib.sha256(authority.encode()).hexdigest()[:24]}"


async def replay_resource_action_receipt(
    db: Any,
    *,
    workspace_id: str,
    command_id: str,
    request_fingerprint: str,
    idempotency_reused_code: str = CRONJOB_IDEMPOTENCY_REUSED,
) -> CommandReceipt | None:
    reader = getattr(db, "get_agent_command", None)
    if not callable(reader):
        return None
    existing = reader(command_id, workspace_id)
    if inspect.isawaitable(existing):
        existing = await existing
    if not isinstance(existing, Mapping):
        return None
    plan = existing.get("payload")
    diff = plan.get("diff") if isinstance(plan, Mapping) else None
    basis = diff.get("basis") if isinstance(diff, Mapping) else None
    if not isinstance(basis, Mapping) or basis.get("request_fingerprint") != request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": idempotency_reused_code,
                "detail": "Idempotency-Key was already used for another resource action.",
            },
        )
    event_id = str(existing.get("confirmation_event_id") or "")
    correlation_id = str(existing.get("correlation_id") or "")
    if not event_id or not correlation_id:
        raise HTTPException(status_code=409, detail="resource action receipt is incomplete")
    return CommandReceipt(
        command_id=command_id,
        event_id=event_id,
        audit_event_id=event_id,
        correlation_id=correlation_id,
        status=str(existing.get("status") or CommandStatus.QUEUED),
    )


@router.post(
    gateway_routes.RESOURCE_WORKLOAD_ROLLBACK_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def rollback_workload(
    resource_id: str,
    payload: WorkloadRollbackRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    if resource_id != payload.resource_id:
        raise HTTPException(status_code=409, detail=WORKLOAD_ROLLBACK_STALE)
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    reader = getattr(db, "get_inventory_resource_by_key", None)
    resource = (
        reader(workspace_id=workspace_id, inventory_key=resource_id) if callable(reader) else None
    )
    if not isinstance(resource, Mapping):
        raise HTTPException(status_code=409, detail=WORKLOAD_ROLLBACK_STALE)
    try:
        current_ref = inventory_resource_ref(resource)
    except HTTPException as exc:
        raise HTTPException(status_code=409, detail=WORKLOAD_ROLLBACK_STALE) from exc
    kind = current_ref.kind.casefold()
    action = WORKLOAD_ROLLBACK_ACTIONS.get(kind)
    cluster_id = str(resource.get("cluster_id") or "")
    namespace = current_ref.namespace
    if (
        action is None
        or namespace is None
        or str(resource.get("snapshot_id") or "") != payload.snapshot_id
        or str(resource.get("resource_version") or "") != payload.workload_resource_version
        or current_ref != payload.workload
    ):
        raise HTTPException(status_code=409, detail=WORKLOAD_ROLLBACK_STALE)
    decision = resource_capabilities_response(
        db,
        workspace_id=workspace_id,
        current=current,
        resource=dict(resource),
    )
    enabled = {item.capability_id for item in decision.capabilities}
    if decision.revision != payload.capability_revision or "workload.rollback" not in enabled:
        raise HTTPException(status_code=409, detail=WORKLOAD_ROLLBACK_STALE)
    selected = workload_revision_selection(
        db,
        workspace_id=workspace_id,
        resource=resource,
        uid=payload.target_revision.uid,
        resource_version=payload.target_resource_version,
    )
    if selected is None:
        raise HTTPException(status_code=409, detail=WORKLOAD_ROLLBACK_STALE)
    revision, target_template = selected
    if (
        revision.resource != payload.target_revision
        or revision.preview_revision != payload.preview_revision
    ):
        raise HTTPException(status_code=409, detail=WORKLOAD_ROLLBACK_STALE)

    request_fingerprint = workload_rollback_request_fingerprint(
        workspace_id=workspace_id,
        user_id=str(current.user_id),
        payload=payload,
    )
    command_id = resource_action_command_id(workspace_id, str(current.user_id), idempotency_key)
    replay = await replay_resource_action_receipt(
        db,
        workspace_id=workspace_id,
        command_id=command_id,
        request_fingerprint=request_fingerprint,
        idempotency_reused_code=WORKLOAD_ROLLBACK_IDEMPOTENCY_REUSED,
    )
    if replay is not None:
        return replay
    diff_basis = {
        "request_fingerprint": request_fingerprint,
        "resource_id": resource_id,
        "snapshot_id": payload.snapshot_id,
        "capability_revision": payload.capability_revision,
        "workload": current_ref.model_dump(),
        "workload_resource_version": payload.workload_resource_version,
        "target_revision": revision.resource.model_dump(),
        "target_resource_version": revision.resource_version,
        "revision": revision.revision,
        "preview_revision": revision.preview_revision,
        "changes": [item.model_dump() for item in revision.changes],
    }
    return await accept_resource_control(
        cluster_id=cluster_id,
        namespace=namespace,
        resource_kind=kind,
        resource_name=current_ref.name,
        action=action,
        reason=payload.reason,
        payload={
            "namespace": namespace,
            "name": current_ref.name,
            "workload_ref": current_ref.model_dump(),
            "workload_resource_version": payload.workload_resource_version,
            "target_revision_ref": revision.resource.model_dump(),
            "target_revision_resource_version": revision.resource_version,
            "target_revision": revision.revision,
            "target_template_sha256": revision.template_sha256,
            "target_template": target_template,
        },
        approval_ref=None,
        policy_decision_ref=None,
        execution_request=payload,
        operation_events=operation_events,
        current=current,
        db=db,
        events=events,
        command_id=command_id,
        diff_basis=diff_basis,
    )


def workload_rollback_request_fingerprint(
    *,
    workspace_id: str,
    user_id: str,
    payload: WorkloadRollbackRequest,
) -> str:
    encoded = json.dumps(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            **payload.model_dump(mode="json"),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


@router.post(
    gateway_routes.CLUSTER_CRONJOB_TRIGGER_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def trigger_cronjob(
    cluster_id: str,
    namespace: str,
    cronjob: str,
    payload: CronJobControlRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_cronjob_control(
        cluster_id=cluster_id,
        namespace=namespace,
        cronjob=cronjob,
        action=Command.KUBERNETES_CRONJOB_TRIGGER_ACTION,
        reason=f"trigger cronjob/{cronjob}",
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_CRONJOB_SUSPEND_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def suspend_cronjob(
    cluster_id: str,
    namespace: str,
    cronjob: str,
    payload: CronJobControlRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_cronjob_control(
        cluster_id=cluster_id,
        namespace=namespace,
        cronjob=cronjob,
        action=Command.KUBERNETES_CRONJOB_SUSPEND_ACTION,
        reason=f"suspend cronjob/{cronjob}",
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.CLUSTER_CRONJOB_RESUME_PATH,
    response_model=CommandReceipt,
    response_model_exclude_none=True,
)
async def resume_cronjob(
    cluster_id: str,
    namespace: str,
    cronjob: str,
    payload: CronJobControlRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandReceipt:
    return await accept_cronjob_control(
        cluster_id=cluster_id,
        namespace=namespace,
        cronjob=cronjob,
        action=Command.KUBERNETES_CRONJOB_RESUME_ACTION,
        reason=f"resume cronjob/{cronjob}",
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(gateway_routes.AGENT_DEBUG_QUERY_PATH, response_model=AgentDebugQueryResponse)
async def agent_debug_query(
    payload: AgentDebugQueryRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> AgentDebugQueryResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_read_access(db, current, workspace_id, payload.cluster_id)
    if is_reserved_log_stream_query(payload.query):
        raise HTTPException(
            status_code=UNPROCESSABLE_CODE,
            detail=RESERVED_LOG_STREAM_QUERY_MESSAGE,
        )
    queued = queue_debug_query(
        db,
        payload,
        workspace_id=workspace_id,
        requested_by=current.user_id,
    )
    return AgentDebugQueryResponse(
        accepted=True,
        command_id=queued.command_id,
        correlation_id=queued.correlation_id,
    )


@router.post(
    gateway_routes.SCOPED_RESOURCE_METRICS_QUERY_PATH,
    response_model=ScopedMetricQueryResponse,
)
async def scoped_resource_metrics_query(
    payload: ScopedMetricQueryRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ScopedMetricQueryResponse:
    """Queue bounded server-owned PromQL for a typed resource/namespace/cluster scope."""
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    require_cluster_read_access(db, current, workspace_id, payload.cluster_id)
    resource_row: dict[str, Any] | None = None
    if payload.subject.kind in {"resource", "pvc"}:
        resource_row = await asyncio.to_thread(
            db.get_inventory_resource_by_key,
            workspace_id=workspace_id,
            inventory_key=payload.subject.resource_id,
        )
    try:
        identity = resolve_scoped_metric_identity(
            payload,
            workspace_id=workspace_id,
            inventory_resource=resource_row,
        )
    except LookupError as exc:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail="metric subject not found") from exc

    if not identity.supported:
        return ScopedMetricQueryResponse(
            availability="unavailable",
            refresh_policy_key=identity.refresh_policy_key,
            scope=identity.scope,
            resource=identity.resource,
            coverage=ScopedMetricCoverage(
                requested=len(payload.categories),
                queued=0,
                unsupported=len(payload.categories),
            ),
            reason_codes=(identity.unavailable_reason or "metric_source_unavailable",),
        )

    plans, unsupported = build_scoped_metric_plans(payload, identity)
    receipts: list[ScopedMetricQueryReceipt] = []
    for plan in plans:
        queued = await asyncio.to_thread(
            queue_debug_query,
            db,
            plan.payload,
            workspace_id=workspace_id,
            requested_by=current.user_id,
        )
        receipts.append(
            ScopedMetricQueryReceipt(
                category=plan.category,
                unit=plan.unit,
                query_name=str(plan.payload.query["name"]),
                command_id=queued.command_id,
                correlation_id=queued.correlation_id,
            )
        )
    coverage = ScopedMetricCoverage(
        requested=len(payload.categories),
        queued=len(receipts),
        unsupported=len(unsupported),
    )
    if not receipts:
        availability = "unavailable"
        reasons = ("metric_category_unsupported",)
    elif unsupported:
        availability = "partial"
        reasons = ("metric_category_partial",)
    else:
        availability = "queued"
        reasons = ()
    return ScopedMetricQueryResponse(
        availability=availability,
        refresh_policy_key=identity.refresh_policy_key,
        scope=identity.scope,
        resource=identity.resource,
        queries=tuple(receipts),
        coverage=coverage,
        reason_codes=reasons,
    )


@router.get(gateway_routes.COMMAND_STATUS_PATH, response_model=CommandStatusResponse)
async def command_status(
    command_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CommandStatusResponse:
    """콘솔이 명령 진행 상태와 agent 가 올린 실제 결과를 폴링 — 임의 완료 표시 제거용."""
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    row = await db.get_agent_command(command_id, workspace_id)
    if row is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    require_cluster_read_access(db, current, workspace_id, str(row["cluster_id"]))
    completed_at = row.get("completed_at")
    return CommandStatusResponse(
        command_id=str(row["command_id"]),
        cluster_id=str(row["cluster_id"]),
        correlation_id=str(row["correlation_id"]),
        action=str(row["action"]),
        status=str(row["status"]),
        result=dict(row.get("result") or {}),
        completed_at=completed_at.isoformat()
        if hasattr(completed_at, "isoformat")
        else (str(completed_at) if completed_at else None),
    )


def command_control_http_error(error: CommandControlError) -> HTTPException:
    if isinstance(error, CommandControlNotFound):
        return HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    return HTTPException(status_code=409, detail=str(error))


def control_receipt_from_existing(control: dict[str, object]) -> CommandControlReceipt:
    details = control.get("details")
    outcome = details if isinstance(details, dict) else {}
    return CommandControlReceipt(
        command_id=str(control["command_id"]),
        action=str(control["action"]),
        event_id=str(control["event_id"]),
        audit_event_id=str(control["audit_event_id"]),
        correlation_id=str(outcome.get("correlation_id") or "control-replayed"),
        status=str(outcome.get("status_after") or CommandStatus.CANCEL_REQUESTED),
        idempotent=True,
        attempt_id=(str(control["attempt_id"]) if control.get("attempt_id") else None),
    )


def command_plan_impact_matches(row: dict[str, object]) -> bool:
    """Direct retry may reuse confirmation only for the identical stored impact."""

    payload = row.get("payload")
    if not isinstance(payload, dict):
        return False
    try:
        expected = command_impact_identity(
            cluster_id=str(row["cluster_id"]),
            action=str(row["action"]),
            namespace=str(payload["namespace"]),
            diff=dict(payload.get("diff") or {}),
            payload=dict(payload.get("payload") or {}),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return expected == str(row.get("impact_identity") or "")


def require_retry_agent_capability(db: Any, *, workspace_id: str, cluster_id: str) -> None:
    """Retry rechecks live agent support; a stale browser capability is never enough."""

    lister = getattr(db, "list_cluster_agent_statuses", None)
    if not callable(lister):
        raise HTTPException(status_code=409, detail="command agent capability cannot be verified")
    statuses = lister(workspace_id, cluster_id)
    if not any(
        isinstance(item, dict)
        and str(item.get("status") or "").lower() == "connected"
        and "command_receiver" in set(item.get("capabilities") or ())
        for item in statuses
    ):
        raise HTTPException(status_code=409, detail="command agent is not currently capable")


async def accept_command_control(
    *,
    command_id: str,
    action: str,
    payload: CommandControlRequest,
    idempotency_key: str,
    current: Any,
    db: Any,
    events: Any,
    operation_events: Any,
) -> CommandControlReceipt:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    row = await db.get_agent_command(command_id, workspace_id)
    if row is None:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    cluster_id = str(row["cluster_id"])
    require_cluster_deploy_access(db, current, workspace_id, cluster_id)
    require_not_management_cluster(
        db,
        workspace_id,
        cluster_id,
        direct_execution=bool(row.get("direct_execution")),
    )

    if action == "retry":
        spec = command_action_spec(str(row["action"]))
        if spec is None or not spec.supports_manual_retry:
            raise HTTPException(
                status_code=409, detail="command action retry policy does not allow manual retry"
            )
        require_retry_agent_capability(db, workspace_id=workspace_id, cluster_id=cluster_id)
        if bool(row.get("direct_execution")):
            if not row.get("confirmation_event_id") or not command_plan_impact_matches(dict(row)):
                raise HTTPException(
                    status_code=409,
                    detail="direct command retry requires a fresh confirmed request",
                )

    body = (
        CommandCancelRequestedBody(
            command_id=command_id,
            workspace_id=workspace_id,
            reason=payload.reason,
            requested_by=current.user_id,
        )
        if action == "cancel"
        else CommandRetryRequestedBody(
            command_id=command_id,
            workspace_id=workspace_id,
            reason=payload.reason,
            requested_by=current.user_id,
        )
    )
    staged: list[Any] = []

    def stage(conn: Any, accepted_event: Any) -> None:
        staged.append(
            stage_command_control_in_transaction(
                conn,
                workspace_id=workspace_id,
                command_id=command_id,
                action=action,
                idempotency_key=idempotency_key,
                requested_by=current.user_id,
                reason=payload.reason,
                event_id=str(accepted_event.event_id),
                audit_event_id=str(accepted_event.event_id),
            )
        )

    try:
        accepted = await events.accept_body(
            body,
            correlation_id=str(row["correlation_id"]),
            actor=Actor(current.user_id, tuple(current.roles)),
            transactional_stage=stage,
        )
    except DuplicateCommandControl as duplicate:
        return control_receipt_from_existing(duplicate.control)
    except CommandControlError as error:
        raise command_control_http_error(error) from error
    if not staged:
        raise HTTPException(status_code=503, detail="command control durable outbox is unavailable")
    control = staged[0]
    await announce_staged_operation_event(
        operation_events,
        control.operation_event,
        workspace_id=workspace_id,
    )
    return CommandControlReceipt(
        command_id=command_id,
        action=action,
        event_id=str(accepted.event.event_id),
        audit_event_id=str(accepted.event.event_id),
        correlation_id=control.correlation_id,
        status=control.status,
        idempotent=False,
        attempt_id=control.attempt_id,
    )


@router.post(
    gateway_routes.COMMAND_CANCEL_PATH,
    response_model=CommandControlReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def cancel_command(
    command_id: str,
    payload: CommandControlRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8)],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandControlReceipt:
    return await accept_command_control(
        command_id=command_id,
        action="cancel",
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.post(
    gateway_routes.COMMAND_RETRY_PATH,
    response_model=CommandControlReceipt,
    response_model_exclude_none=True,
    status_code=202,
)
async def retry_command(
    command_id: str,
    payload: CommandControlRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8)],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> CommandControlReceipt:
    return await accept_command_control(
        command_id=command_id,
        action="retry",
        payload=payload,
        idempotency_key=idempotency_key,
        current=current,
        db=db,
        events=events,
        operation_events=operation_events,
    )


@router.get(gateway_routes.COMMAND_EVENTS_PATH)
async def command_events(
    command_id: str,
    after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    operation_events: Any = Depends(get_operation_events),
) -> StreamingResponse:
    """Replay durable events then follow live broker notifications in exact order."""
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    cursor = operation_event_cursor(after, last_event_id)
    subscribe = getattr(operation_events, "subscribe", None)
    if not callable(subscribe):
        raise HTTPException(status_code=503, detail="operation event stream unavailable")
    # Subscribe before reading replay so a commit between the two is either in
    # the query result or this live queue; no status polling recovery exists.
    subscription = await subscribe(command_id, workspace_id=workspace_id)
    list_events = getattr(db, "list_command_operation_events", None)
    event_context = getattr(db, "get_command_operation_event_context", None)

    async def replay(starting_after: int) -> list[OperationEvent]:
        if callable(list_events):
            return await list_events(
                workspace_id,
                command_id,
                after_sequence=starting_after,
            )
        return []

    try:
        initial = await replay(cursor)
        if initial:
            cluster_id = str(initial[0].payload.get("cluster_id") or "")
            if not cluster_id:
                raise HTTPException(
                    status_code=500, detail="operation event missing cluster identity"
                )
            require_cluster_read_access(db, current, workspace_id, cluster_id)
        else:
            # A receipt is written ahead of the asynchronous agent_commands
            # projection.  Reconnecting from that receipt is valid: authorize
            # from the immutable operation ledger and keep the SSE subscription
            # open until a later lifecycle event is committed.
            context = (
                await event_context(workspace_id, command_id) if callable(event_context) else None
            )
            if context is not None:
                last_sequence = int(context.get("last_sequence") or 0)
                cluster_id = str(context.get("cluster_id") or "")
                if not cluster_id:
                    raise HTTPException(
                        status_code=500, detail="operation event missing cluster identity"
                    )
                if cursor > last_sequence:
                    raise HTTPException(
                        status_code=UNPROCESSABLE_CODE,
                        detail="operation event cursor is ahead of durable history",
                    )
                require_cluster_read_access(db, current, workspace_id, cluster_id)
            else:
                # Existing commands predating the append-only ledger are materialized
                # as one compatibility snapshot only. New receipts always have an
                # accepted event and therefore never take this delayed-row path.
                row = await db.get_agent_command(command_id, workspace_id)
                if row is None:
                    raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
                require_cluster_read_access(db, current, workspace_id, str(row["cluster_id"]))
                initial = [command_snapshot_event(dict(row))]
    except BaseException:
        await subscription.close()
        raise

    async def stream():
        delivered = cursor

        async def emit(events: list[OperationEvent]):
            nonlocal delivered
            for event in events:
                if event.sequence <= delivered:
                    continue
                if delivered and event.sequence != delivered + 1:
                    raise RuntimeError("durable operation events are not contiguous")
                yield sse_operation_event(event)
                delivered = event.sequence
                if event.kind in {"completed", "failed", "cancelled"}:
                    return

        try:
            async for item in emit(initial):
                yield item
            if initial and initial[-1].kind in {"completed", "failed", "cancelled"}:
                return
            while True:
                try:
                    live = await asyncio.wait_for(
                        subscription.next(), timeout=OPERATION_EVENT_REPLAY_POLL_SECONDS
                    )
                except TimeoutError:
                    # Redis is only a low-latency wake-up. A failed listener or
                    # cross-replica publish is repaired from PostgreSQL without
                    # asking the browser to poll command status.
                    replayed = await replay(delivered)
                    async for item in emit(replayed):
                        yield item
                    if replayed and replayed[-1].kind in {"completed", "failed", "cancelled"}:
                        return
                    yield ": keep-alive\n\n"
                    continue
                if live.sequence <= delivered:
                    continue
                if live.sequence != delivered + 1:
                    async for item in emit(await replay(delivered)):
                        yield item
                    if live.sequence <= delivered:
                        continue
                if live.sequence != delivered + 1:
                    raise RuntimeError("durable operation event replay did not close sequence gap")
                yield sse_operation_event(live)
                delivered = live.sequence
                if live.kind in {"completed", "failed", "cancelled"}:
                    return
        finally:
            await subscription.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# agent 라우트 — 각 핸들러의 identity dependency 로 per-cluster 토큰 인증.
# workspace_id/cluster_id 는 토큰으로 인증된 identity 에서만 취하고 body/query 는 신뢰 안 함.
agent_router = APIRouter()


@agent_router.get(gateway_routes.AGENT_COMMAND_POLL_PATH, response_model=AgentCommandPollResponse)
async def poll_command(
    agent_id: str = "target-agent",
    timeout: int = DEFAULT_POLL_SECONDS,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
    operation_events: Any = Depends(get_operation_events),
) -> AgentCommandPollResponse:
    # 멀티클러스터: 각 클러스터 agent 가 자기 cluster_id 로 아웃바운드 롱폴(인바운드 0).
    # cluster_id/workspace_id 는 토큰 identity 에서 — 임의 클러스터/워크스페이스 폴링 차단.
    leased = await lease_next_command(
        db, identity.cluster_id, identity.workspace_id, agent_id, timeout
    )
    row = leased.command if isinstance(leased, LeasedAgentCommand) else leased
    if row is not None:
        if not await announce_staged_operation_event(
            operation_events,
            leased.operation_event if isinstance(leased, LeasedAgentCommand) else None,
            workspace_id=identity.workspace_id,
        ):
            await publish_operation_event(
                operation_events,
                command_id=str(row["command_id"]),
                workspace_id=identity.workspace_id,
                status=CommandStatus.LEASED,
                payload={
                    "cluster_id": identity.cluster_id,
                    "action": str(row["action"]),
                    "correlation_id": str(row["correlation_id"]),
                    "attempt_id": row.get("attempt_id"),
                },
            )
    return AgentCommandPollResponse(command=row)


@agent_router.post(gateway_routes.AGENT_COMMAND_START_PATH, response_model=CommandStartedResponse)
async def command_start(
    command_id: str,
    payload: CommandStartRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
    operation_events: Any = Depends(get_operation_events),
) -> CommandStartedResponse:
    start = db.start_agent_command
    if payload.attempt_id is None:
        correlation_id = await async_retry_db_conflict(
            lambda: start(
                command_id,
                identity.workspace_id,  # body 가 아닌 토큰 identity 의 workspace
                identity.cluster_id,  # body 가 아닌 토큰 identity 의 cluster
                payload.lease_id,
                payload.agent_id,
                CommandStatus.RUNNING,
                LEASE_SECONDS,
            )
        )
    else:
        correlation_id = await async_retry_db_conflict(
            lambda: start(
                command_id,
                identity.workspace_id,
                identity.cluster_id,
                payload.lease_id,
                payload.agent_id,
                CommandStatus.RUNNING,
                LEASE_SECONDS,
                payload.attempt_id,
            )
        )
    if not correlation_id:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    correlation = (
        correlation_id if isinstance(correlation_id, str) else str(correlation_id.correlation_id)
    )
    if not correlation:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    if not await announce_staged_operation_event(
        operation_events,
        correlation_id.operation_event if isinstance(correlation_id, StartedAgentCommand) else None,
        workspace_id=identity.workspace_id,
    ):
        await publish_operation_event(
            operation_events,
            command_id=command_id,
            workspace_id=identity.workspace_id,
            status=CommandStatus.RUNNING,
            payload={"cluster_id": identity.cluster_id, "correlation_id": correlation},
        )
    return CommandStartedResponse(accepted=True, correlation_id=correlation)


@agent_router.post(
    gateway_routes.AGENT_COMMAND_HEARTBEAT_PATH, response_model=CommandHeartbeatResponse
)
async def command_heartbeat(
    command_id: str,
    payload: CommandHeartbeatRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
    operation_events: Any = Depends(get_operation_events),
) -> CommandHeartbeatResponse:
    heartbeat = db.heartbeat_agent_command
    if payload.attempt_id is None and payload.observed_cancel_generation is None:
        correlation_id = await async_retry_db_conflict(
            lambda: heartbeat(
                command_id,
                identity.workspace_id,
                identity.cluster_id,
                payload.lease_id,
                payload.agent_id,
                LEASE_SECONDS,
            )
        )
    else:
        correlation_id = await async_retry_db_conflict(
            lambda: heartbeat(
                command_id,
                identity.workspace_id,
                identity.cluster_id,
                payload.lease_id,
                payload.agent_id,
                LEASE_SECONDS,
                payload.attempt_id,
                payload.observed_cancel_generation,
            )
        )
    if not correlation_id:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    # Compatibility stores used by old deployments return only the correlation
    # string.  Production repository returns the full reverse-control reply.
    correlation = (
        correlation_id
        if isinstance(correlation_id, str)
        else str(getattr(correlation_id, "correlation_id", ""))
    )
    if not correlation:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    await announce_staged_operation_event(
        operation_events,
        getattr(correlation_id, "operation_event", None),
        workspace_id=identity.workspace_id,
    )
    if payload.progress is not None:
        progress_event = await db.append_command_operation_event(
            identity.workspace_id,
            command_id,
            "progress",
            {
                "cluster_id": identity.cluster_id,
                "correlation_id": correlation,
                "status": CommandStatus.RUNNING,
                "progress": payload.progress.model_dump(),
            },
        )
        await announce_staged_operation_event(
            operation_events,
            progress_event,
            workspace_id=identity.workspace_id,
        )
    return CommandHeartbeatResponse(
        accepted=True,
        correlation_id=correlation,
        cancel_requested=bool(getattr(correlation_id, "cancel_requested", False)),
        cancel_generation=getattr(correlation_id, "cancel_generation", None),
    )


@agent_router.post(gateway_routes.AGENT_COMMAND_RESULT_PATH, response_model=EventIdAcceptedResponse)
async def command_result(
    command_id: str,
    payload: CommandResultRequest,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
    operation_events: Any = Depends(get_operation_events),
) -> EventIdAcceptedResponse:
    command_row = await db.get_agent_command(command_id, identity.workspace_id)
    if command_row is None or str(command_row.get("cluster_id")) != identity.cluster_id:
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    uninstall_result = str(command_row.get("action")) == Command.CLUSTER_AGENT_UNINSTALL_ACTION
    if uninstall_result:
        registration_getter = getattr(db, "get_cluster_registration", None)
        registration = (
            registration_getter(identity.workspace_id, identity.cluster_id)
            if callable(registration_getter)
            else None
        )
        if is_management_registration(registration):
            raise HTTPException(status_code=400, detail=management_readonly_detail())
    result = payload.model_dump()
    result["workspace_id"] = identity.workspace_id
    result["cluster_id"] = identity.cluster_id
    complete = db.complete_agent_command_and_stage_event
    if payload.attempt_id is None:
        completed = await async_retry_db_conflict(
            lambda: complete(
                command_id,
                identity.workspace_id,
                identity.cluster_id,
                result,
                payload.lease_id,
                payload.agent_id,
                "api-gateway",
            )
        )
    else:
        completed = await async_retry_db_conflict(
            lambda: complete(
                command_id,
                identity.workspace_id,
                identity.cluster_id,
                result,
                payload.lease_id,
                payload.agent_id,
                "api-gateway",
                payload.attempt_id,
            )
        )
    if completed is None:
        stored_result = command_row.get("result")
        replayed_verified_uninstall = (
            uninstall_result
            and str(command_row.get("status")) == CommandStatus.COMPLETED
            and isinstance(stored_result, Mapping)
            and payload.status == CommandStatus.COMPLETED
            and payload.cleanup_completed is True
            and payload.cleanup_resources == list(UNINSTALL_CLEANUP_RESOURCE_REFS)
            and payload.residual_resources == []
            and stored_result.get("status") == CommandStatus.COMPLETED
            and stored_result.get("cleanup_completed") is True
            and stored_result.get("cleanup_resources") == list(UNINSTALL_CLEANUP_RESOURCE_REFS)
            and stored_result.get("residual_resources") == []
        )
        if replayed_verified_uninstall:
            unregister = getattr(db, "unregister_target_cluster", None)
            if not callable(unregister) or not unregister(
                identity.workspace_id, identity.cluster_id
            ):
                raise HTTPException(
                    status_code=500,
                    detail="agent uninstall ACK was stored but registration revocation failed",
                )
            return EventIdAcceptedResponse(
                accepted=True,
                event_id=str(command_row.get("terminal_event_id") or command_id),
            )
        raise HTTPException(status_code=NOT_FOUND_CODE, detail=NOT_FOUND_MESSAGE)
    if not await announce_staged_operation_event(
        operation_events,
        getattr(completed, "operation_event", None),
        workspace_id=identity.workspace_id,
    ):
        await publish_operation_event(
            operation_events,
            command_id=command_id,
            workspace_id=identity.workspace_id,
            status=payload.status,
            payload={
                "cluster_id": identity.cluster_id,
                "correlation_id": str(command_row.get("correlation_id") or ""),
                "result": result,
            },
        )
    if (
        uninstall_result
        and payload.status == CommandStatus.COMPLETED
        and payload.cleanup_completed is True
        and payload.cleanup_resources == list(UNINSTALL_CLEANUP_RESOURCE_REFS)
        and payload.residual_resources == []
    ):
        unregister = getattr(db, "unregister_target_cluster", None)
        if not callable(unregister) or not unregister(identity.workspace_id, identity.cluster_id):
            raise HTTPException(
                status_code=500,
                detail="agent uninstall ACK was stored but registration revocation failed",
            )
    return EventIdAcceptedResponse(accepted=True, event_id=completed.event_id)


router.include_router(agent_router)
