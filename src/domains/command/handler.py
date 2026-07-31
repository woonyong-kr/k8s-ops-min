from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from domains.command.actions import allowed_command_actions, command_action_spec
from domains.command.events import (
    CommandCompletedBody,
    CommandDispatchedBody,
    CommandQueuedForAgentBody,
    CommandRejectedBody,
    CommandRequestedBody,
    LeaseMetadata,
    Plan,
    RetryPolicy,
    Route,
    RoutingConstraint,
)
from domains.command.policy import (
    DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_COMMAND_LEASE_SECONDS,
    DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
    DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
    CommandConfig,
    ModelLookup,
    NamespaceAllowlistRule,
    Policy,
    PolicyRuleConfig,
)
from domains.command.policy import Result as PolicyResult
from domains.target.management_guard import (
    cluster_role_from_policy,
    is_management_registration,
    is_management_role,
    management_readonly_detail,
)
from packages.config.constants import Command, CommandStatus, Sandbox, Target
from packages.config.control import (
    CONTROL_NAMESPACE_DENIED_CODE,
    CONTROL_NAMESPACE_DENIED_MESSAGE,
)
from packages.config.environments import is_sandbox_environment, normalize_environment
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.security import env_enabled
from packages.config.settings import env
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.fields import Gateway
from packages.contracts.gitops import ApprovalStatus
from packages.contracts.stores import AgentCommandStore
from packages.runtime.app import EventContext

COMMAND_CONFIG = CommandConfig(
    service_name="command-worker",
    agent_route_channel="agent-poll",
    policy_steps=("validate policy", "route target cluster", "queue for agent"),
    default_namespace=Sandbox.NAMESPACE,
    default_cluster_id=Target.DEFAULT_CLUSTER_ID,
    default_command_action=Command.DEFAULT_ACTION,
    command_status_queued=CommandStatus.QUEUED,
    lease_seconds=DEFAULT_COMMAND_LEASE_SECONDS,
    heartbeat_interval_seconds=DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
    retry_max_attempts=DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
    retry_delay_seconds=DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
    required_agent_capability="command_receiver",
    policy_rules=(
        PolicyRuleConfig(
            name="command_action_allowlist",
            field=Gateway.ACTION,
            allowed_values=allowed_command_actions(),
            default=Command.DEFAULT_ACTION,
            reason="unsupported command action",
        ),
    ),
)
# 네임스페이스 룰은 정적 값 비교가 아니라 제어 허용목록(CONTROL_ALLOWED_NAMESPACES,
# 기본 sandbox 만)을 평가 시점에 읽는다 — packages.config.control 이 단일 기준.
POLICY = Policy(
    (
        NamespaceAllowlistRule(
            name=CONTROL_NAMESPACE_DENIED_CODE,
            field=Gateway.NAMESPACE,
            default_namespace=Sandbox.NAMESPACE,
            reason=CONTROL_NAMESPACE_DENIED_MESSAGE,
        ),
        *Policy.build(COMMAND_CONFIG.policy_rules).rules,
    )
)
NAMESPACE_MISMATCH_REASON = "command namespace must match diff namespace"
MANIFEST_NAMESPACE_MISMATCH_REASON = "manifest namespace must match command namespace"
ACTION_NAMESPACE_REASON = "namespace not allowed for this command action"
MISSING_APPROVAL_REF_REASON = "write command requires approval_ref"
MISSING_POLICY_DECISION_REF_REASON = "write command requires policy_decision_ref"
APPROVAL_RECORD_MISSING_REASON = "write command approval_ref is not recorded"
APPROVAL_NOT_GRANTED_REASON = "write command approval_ref is not granted"
APPROVAL_NOT_REQUIRED_SCOPE_REASON = (
    "write command not-required approval is limited to sandbox safe_pr policy"
)
APPROVAL_POLICY_DECISION_MISMATCH_REASON = "write command policy_decision_ref mismatch"
APPROVAL_WORKFLOW_MISMATCH_REASON = "write command approval workflow mismatch"
APPROVAL_DECIDED_BY_MISSING_REASON = "write command approval_decided_by is missing"
APPROVAL_DECIDED_BY_MISMATCH_REASON = "write command approval_decided_by mismatch"
APPROVAL_EXPIRED_REASON = "write command approval is expired"
APPROVAL_EXPIRES_AT_INVALID_REASON = "write command approval_expires_at is invalid"
DIRECT_EXECUTION_CONFIRMATION_REQUIRED_REASON = "direct command requires explicit confirmation"
MANAGEMENT_READONLY_REASON = management_readonly_detail()["code"]
COMMAND_APPROVAL_EVIDENCE_TTL_SECONDS_ENV = "COMMAND_APPROVAL_EVIDENCE_TTL_SECONDS"
DEFAULT_COMMAND_APPROVAL_EVIDENCE_TTL_SECONDS = "3600"
AUTO_COMMANDS_ENABLED_ENV = "AUTO_COMMANDS_ENABLED"
AUTO_COMMANDS_DISABLED_REASON = f"AI automatic commands require {AUTO_COMMANDS_ENABLED_ENV}=1"
LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class ApprovalEvidence:
    approval_ref: str
    policy_decision_ref: str
    decided_by: str
    expires_at: str


def desired_manifest_namespace(command: CommandRequestedBody) -> str | None:
    metadata = command.diff.desired_manifest.get("metadata")
    if not isinstance(metadata, dict):
        return None
    namespace = metadata.get(Gateway.NAMESPACE)
    if namespace in (None, ""):
        return None
    return str(namespace)


def evaluate_command_policy(command: CommandRequestedBody) -> PolicyResult:
    if command.diff.is_image_only_noop():
        return PolicyResult.reject(Sandbox.NO_DIFF_REASON)
    if command.namespace != command.diff.namespace:
        return PolicyResult.reject(NAMESPACE_MISMATCH_REASON)
    manifest_namespace = desired_manifest_namespace(command)
    if manifest_namespace is not None and manifest_namespace != command.namespace:
        return PolicyResult.reject(MANIFEST_NAMESPACE_MISMATCH_REASON)
    # 액션별 정책 메타데이터(@command.action allowed_namespaces) — 카탈로그가 기준.
    spec = command_action_spec(command.action)
    if spec is None:
        return PolicyResult.reject("unsupported command action")
    result = PolicyResult.allow()
    if spec.enforce_control_namespace:
        result = POLICY.evaluate(ModelLookup(command))
        if not result.allowed:
            return result
    elif command.action not in allowed_command_actions():
        return PolicyResult.reject("unsupported command action")
    if spec is not None and not spec.allows_namespace(command.namespace):
        return PolicyResult.reject(ACTION_NAMESPACE_REASON)
    if (
        spec is not None
        and spec.requires_approval_for(command.namespace)
        and not command.direct_execution
        and not approval_exempt_for_environment(command)
    ):
        if not command.approval_ref:
            return PolicyResult.reject(MISSING_APPROVAL_REF_REASON)
        if not command.policy_decision_ref:
            return PolicyResult.reject(MISSING_POLICY_DECISION_REF_REASON)
    return result


AUTO_APPROVE_ACTIONS_ENV = "COMMAND_AUTO_APPROVE_ACTIONS"
AUTO_APPROVE_ENVIRONMENTS_ENV = "COMMAND_AUTO_APPROVE_ENVIRONMENTS"
DEFAULT_AUTO_APPROVE_ACTIONS = Command.KUBERNETES_DEPLOYMENT_SCALE_ACTION
DEFAULT_AUTO_APPROVE_ENVIRONMENTS = "sandbox"
COMMAND_PRIORITY_HIGH = 100


def _csv_values(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def approval_exempt_for_environment(command: CommandRequestedBody) -> bool:
    """sandbox 허용 rule — 지정 액션은 sandbox 환경에서 승인 기록 없이 실행을 허용함.

    기본값: k8s deployment scale 만 sandbox 환경 면제.
    production 등 다른 환경은 environment 불일치로 면제되지 않으며,
    COMMAND_AUTO_APPROVE_ACTIONS / COMMAND_AUTO_APPROVE_ENVIRONMENTS 로 조정한다.
    카탈로그 allowed_namespaces·에이전트 namespace/resource 정책은 그대로 적용된다.
    """
    actions = _csv_values(env(AUTO_APPROVE_ACTIONS_ENV, DEFAULT_AUTO_APPROVE_ACTIONS))
    environments = {
        normalize_environment(item)
        for item in _csv_values(
            env(AUTO_APPROVE_ENVIRONMENTS_ENV, DEFAULT_AUTO_APPROVE_ENVIRONMENTS)
        )
    }
    return command.action in actions and normalize_environment(command.environment) in environments


def command_requires_recorded_approval(command: CommandRequestedBody) -> bool:
    if command.direct_execution:
        return False
    spec = command_action_spec(command.action)
    if spec is None or not spec.requires_approval_for(command.namespace):
        return False
    return not approval_exempt_for_environment(command)


def evaluate_direct_execution_confirmation(command: CommandRequestedBody) -> PolicyResult:
    if command.direct_execution and not command.direct_execution_confirmed:
        return PolicyResult.reject(DIRECT_EXECUTION_CONFIRMATION_REQUIRED_REASON)
    return PolicyResult.allow()


def utc_now() -> datetime:
    return datetime.now(UTC)


def approval_evidence_ttl_seconds() -> int:
    raw = env(
        COMMAND_APPROVAL_EVIDENCE_TTL_SECONDS_ENV,
        DEFAULT_COMMAND_APPROVAL_EVIDENCE_TTL_SECONDS,
    )
    try:
        value = int(raw)
    except ValueError:
        return int(DEFAULT_COMMAND_APPROVAL_EVIDENCE_TTL_SECONDS)
    return max(1, value)


def parse_approval_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def approval_timestamp_body(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def approval_expires_at_from_record(record: JsonObject) -> tuple[PolicyResult, str | None]:
    raw = record.get("expires_at")
    if raw in (None, ""):
        return PolicyResult.allow(), approval_timestamp_body(
            utc_now() + timedelta(seconds=approval_evidence_ttl_seconds())
        )
    parsed = parse_approval_timestamp(raw)
    if parsed is None:
        return PolicyResult.reject(APPROVAL_EXPIRES_AT_INVALID_REASON), None
    if parsed <= utc_now():
        return PolicyResult.reject(APPROVAL_EXPIRED_REASON), None
    return PolicyResult.allow(), approval_timestamp_body(parsed)


def approval_status_result(command: CommandRequestedBody, record: JsonObject) -> PolicyResult:
    status = str(record.get("status", ""))
    if status == ApprovalStatus.GRANTED.value:
        return PolicyResult.allow()
    if status != ApprovalStatus.NOT_REQUIRED.value:
        return PolicyResult.reject(APPROVAL_NOT_GRANTED_REASON)

    details = record.get("details")
    policy_route = str(details.get("policy_route", "")) if isinstance(details, dict) else ""
    if is_sandbox_environment(command.environment) and policy_route == "safe_pr":
        return PolicyResult.allow()
    return PolicyResult.reject(APPROVAL_NOT_REQUIRED_SCOPE_REASON)


async def evaluate_recorded_approval(
    command: CommandRequestedBody, db: AgentCommandStore
) -> tuple[PolicyResult, ApprovalEvidence | None]:
    if not command_requires_recorded_approval(command):
        return PolicyResult.allow(), None
    if not command.approval_ref:
        return PolicyResult.reject(MISSING_APPROVAL_REF_REASON), None
    if not command.policy_decision_ref:
        return PolicyResult.reject(MISSING_POLICY_DECISION_REF_REASON), None

    getter = getattr(db, "get_workflow_approval", None)
    if getter is None:
        return PolicyResult.reject(APPROVAL_RECORD_MISSING_REASON), None
    record = await getter(command.approval_ref, command.workspace_id)
    if not isinstance(record, dict):
        return PolicyResult.reject(APPROVAL_RECORD_MISSING_REASON), None

    if str(record.get("workflow_run_id", "")) != command.workflow_run_id:
        return PolicyResult.reject(APPROVAL_WORKFLOW_MISMATCH_REASON), None
    status_result = approval_status_result(command, record)
    if not status_result.allowed:
        return status_result, None

    details = record.get("details")
    if isinstance(details, dict):
        recorded_ref = details.get("policy_decision_ref")
        if recorded_ref and str(recorded_ref) != command.policy_decision_ref:
            return PolicyResult.reject(APPROVAL_POLICY_DECISION_MISMATCH_REASON), None
        recorded_approval = details.get("approval_ref")
        if recorded_approval and str(recorded_approval) != command.approval_ref:
            return PolicyResult.reject(APPROVAL_POLICY_DECISION_MISMATCH_REASON), None

    decided_by = str(record.get("decided_by") or "")
    if not decided_by:
        return PolicyResult.reject(APPROVAL_DECIDED_BY_MISSING_REASON), None
    if command.approval_decided_by and command.approval_decided_by != decided_by:
        return PolicyResult.reject(APPROVAL_DECIDED_BY_MISMATCH_REASON), None

    expires_result, expires_at = approval_expires_at_from_record(record)
    if not expires_result.allowed:
        return expires_result, None
    assert expires_at is not None
    if command.approval_expires_at:
        requested_expires_at = parse_approval_timestamp(command.approval_expires_at)
        if requested_expires_at is None:
            return PolicyResult.reject(APPROVAL_EXPIRES_AT_INVALID_REASON), None
        if approval_timestamp_body(requested_expires_at) != expires_at:
            return PolicyResult.reject(APPROVAL_EXPIRES_AT_INVALID_REASON), None

    return (
        PolicyResult.allow(),
        ApprovalEvidence(
            approval_ref=command.approval_ref,
            policy_decision_ref=command.policy_decision_ref,
            decided_by=decided_by,
            expires_at=expires_at,
        ),
    )


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


async def evaluate_management_guard(
    command: CommandRequestedBody, db: AgentCommandStore
) -> PolicyResult:
    if command.direct_execution:
        return PolicyResult.allow()
    spec = command_action_spec(command.action)
    if spec is not None and spec.read_only:
        return PolicyResult.allow()
    registration_getter = getattr(db, "get_cluster_registration", None)
    registration = None
    if callable(registration_getter):
        registration = await _maybe_await(
            registration_getter(command.workspace_id, command.cluster_id)
        )
    policy_getter = getattr(db, "get_cluster_policy", None)
    policy = None
    if callable(policy_getter):
        policy = await _maybe_await(policy_getter(command.workspace_id, command.cluster_id))
    if is_management_registration(registration) or is_management_role(
        cluster_role_from_policy(policy)
    ):
        LOGGER.warning(
            "management_write_command_rejected",
            extra={
                CONTEXT_KEY: {
                    "workspace_id": command.workspace_id,
                    "cluster_id": command.cluster_id,
                    "action": command.action,
                }
            },
        )
        return PolicyResult.reject(MANAGEMENT_READONLY_REASON)
    return PolicyResult.allow()


def idempotency_key(
    command: CommandRequestedBody,
    correlation_id: str,
    approval_evidence: ApprovalEvidence | None = None,
) -> str:
    payload = {
        "correlation_id": correlation_id,
        "workspace_id": command.workspace_id,
        "application_id": command.application_id,
        "workflow_run_id": command.workflow_run_id,
        "binding_id": command.binding_id,
        "environment": command.environment,
        "cluster_id": command.cluster_id,
        "action": command.action,
        "namespace": command.namespace,
        "direct_execution": command.direct_execution,
        "direct_execution_confirmed": command.direct_execution_confirmed,
        "approval_ref": command.approval_ref,
        "policy_decision_ref": command.policy_decision_ref,
        "approval_decided_by": (
            approval_evidence.decided_by if approval_evidence else command.approval_decided_by
        ),
        "approval_expires_at": (
            approval_evidence.expires_at if approval_evidence else command.approval_expires_at
        ),
        "diff": command.diff.to_body(),
        "payload": command.payload,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_plan(
    command: CommandRequestedBody,
    correlation_id: str,
    approval_evidence: ApprovalEvidence | None = None,
) -> Plan:
    key = idempotency_key(command, correlation_id, approval_evidence)
    cluster_id = command.cluster_id or COMMAND_CONFIG.default_cluster_id
    workspace_id = command.workspace_id
    action = command.action or COMMAND_CONFIG.default_command_action
    action_spec = command_action_spec(action)
    return Plan(
        # API가 접수 UoW에서 만든 ID는 approval evidence가 나중에 보강돼도 절대
        # 바뀌지 않는다. 과거/외부 이벤트만 기존 hash ID fallback을 유지한다.
        command_id=command.command_id or f"cmd-{key[:32]}",
        idempotency_key=key,
        cluster_id=cluster_id,
        action=action,
        namespace=command.namespace or COMMAND_CONFIG.default_namespace,
        diff=command.diff.to_body(),
        payload=command.payload,
        steps=list(COMMAND_CONFIG.policy_steps),
        lease=LeaseMetadata(
            lease_seconds=COMMAND_CONFIG.lease_seconds,
            heartbeat_interval_seconds=COMMAND_CONFIG.heartbeat_interval_seconds,
        ),
        retry_policy=RetryPolicy(
            max_attempts=(
                action_spec.max_attempts
                if action_spec is not None and action_spec.supports_manual_retry
                else 1
            ),
            retry_delay_seconds=(action_spec.retry_delay_seconds if action_spec is not None else 0),
        ),
        routing_constraint=RoutingConstraint(
            channel=COMMAND_CONFIG.agent_route_channel,
            cluster_id=cluster_id,
            workspace_id=workspace_id,
            required_capability=(
                action_spec.required_agent_capability
                if action_spec is not None
                else COMMAND_CONFIG.required_agent_capability
            ),
        ),
        workspace_id=workspace_id,
        application_id=command.application_id,
        workflow_run_id=command.workflow_run_id,
        binding_id=command.binding_id,
        environment=command.environment,
        priority=max(COMMAND_PRIORITY_HIGH, int(command.priority or COMMAND_PRIORITY_HIGH)),
        approval_ref=command.approval_ref,
        policy_decision_ref=command.policy_decision_ref,
        approval_decided_by=(
            approval_evidence.decided_by if approval_evidence else command.approval_decided_by
        ),
        approval_expires_at=(
            approval_evidence.expires_at if approval_evidence else command.approval_expires_at
        ),
        direct_execution=command.direct_execution,
        direct_execution_confirmed=command.direct_execution_confirmed,
    )


def route_for_plan(plan: Plan) -> Route:
    return Route(channel=plan.routing_constraint.channel, cluster_id=plan.cluster_id)


async def queue_plan_for_agent(ctx: EventContext[AgentCommandStore], plan: Plan) -> bool:
    return await ctx.db.queue_agent_command(
        ctx.correlation_id, plan.to_body(), COMMAND_CONFIG.command_status_queued
    )


async def close_operation(ctx: EventContext[AgentCommandStore], plan: Plan, reason: str) -> None:
    """Persist a terminal lifecycle fact when the command cannot reach an agent."""
    fail_logical = getattr(ctx.db, "fail_logical_command_and_stage_event", None)
    if callable(fail_logical):
        await fail_logical(plan.workspace_id, plan.command_id, plan.cluster_id, reason)
        return
    append = getattr(ctx.db, "append_command_operation_event", None)
    if not callable(append):
        return
    await append(
        plan.workspace_id,
        plan.command_id,
        "failed",
        {
            "cluster_id": plan.cluster_id,
            "status": CommandStatus.FAILED,
            "reason": reason,
        },
    )


async def reject_operation(
    ctx: EventContext[AgentCommandStore],
    command: CommandRequestedBody,
    reason: str,
    reason_code: str | None = None,
) -> CommandRejectedBody:
    """Close the receipt's immutable operation stream for every worker rejection."""
    await close_operation(ctx, build_plan(command, ctx.correlation_id), reason)
    return CommandRejectedBody(
        reason=reason,
        reason_code=reason_code,
        requested=command.to_body(),
    )


async def sweep_expired_agent_commands(
    ctx: EventContext[AgentCommandStore],
) -> AsyncIterator[EventBody]:
    """만료 방치 명령 janitor — 명령 이벤트 처리 길목에서 기회적으로 수행함.

    전용 스케줄러 없이 command-worker 의 이벤트 경로에 편승 — 종결된 명령마다
    CommandCompleted(FAILED)를 흘려 workflow 가 영구 APPLYING 에 갇히지 않게 함.
    """
    expired = await ctx.db.fail_expired_agent_commands() or []
    for row in expired:
        yield CommandCompletedBody(command_id=str(row["command_id"]), result=dict(row["result"]))


async def handle_command_requested(
    evt: CommandRequestedBody, ctx: EventContext[AgentCommandStore]
) -> AsyncIterator[EventBody]:
    if (
        isinstance(evt.actor, dict)
        and evt.actor.get("auto_selected") is True
        and not env_enabled(AUTO_COMMANDS_ENABLED_ENV)
    ):
        yield await reject_operation(ctx, evt, AUTO_COMMANDS_DISABLED_REASON)
        return
    management_result = await evaluate_management_guard(evt, ctx.db)
    if not management_result.allowed:
        yield await reject_operation(
            ctx,
            evt,
            management_result.require_reason(),
            management_result.reason_code,
        )
        return
    direct_execution_result = evaluate_direct_execution_confirmation(evt)
    if not direct_execution_result.allowed:
        yield await reject_operation(
            ctx,
            evt,
            direct_execution_result.require_reason(),
            direct_execution_result.reason_code,
        )
        return
    result = evaluate_command_policy(evt)
    if not result.allowed:
        yield await reject_operation(ctx, evt, result.require_reason(), result.reason_code)
        return
    approval_result, approval_evidence = await evaluate_recorded_approval(evt, ctx.db)
    if not approval_result.allowed:
        yield await reject_operation(
            ctx,
            evt,
            approval_result.require_reason(),
            approval_result.reason_code,
        )
        return

    plan = build_plan(evt, ctx.correlation_id, approval_evidence)
    yield CommandDispatchedBody(plan=plan, route=route_for_plan(plan))
    inserted = await queue_plan_for_agent(ctx, plan)
    if not inserted:
        await close_operation(ctx, plan, "agent command could not be queued")
        yield CommandRejectedBody(
            reason="agent command could not be queued", requested=evt.to_body()
        )
        return
    yield CommandQueuedForAgentBody(
        command_id=plan.command_id,
        cluster_id=plan.cluster_id,
        workspace_id=plan.workspace_id,
        application_id=plan.application_id,
        workflow_run_id=plan.workflow_run_id,
        binding_id=plan.binding_id,
        environment=plan.environment,
        priority=plan.priority,
        approval_ref=plan.approval_ref,
        policy_decision_ref=plan.policy_decision_ref,
        approval_decided_by=plan.approval_decided_by,
        approval_expires_at=plan.approval_expires_at,
        direct_execution=plan.direct_execution,
        direct_execution_confirmed=plan.direct_execution_confirmed,
    )
