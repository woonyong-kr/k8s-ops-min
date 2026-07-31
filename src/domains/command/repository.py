"""command DB 저장소 — agent_commands 조작을 감싸 라우터/워커와 SQLAlchemy 세부 구현 분리."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, and_, case, cast, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.command.actions import command_action_spec
from domains.command.events import CommandCompletedBody
from domains.command.lifecycle import command_impact_identity, command_terminal_event_kind
from domains.command.models import (
    AgentCommand,
    AgentCommandAttempt,
    CommandControlAction,
    CommandOperationEvent,
    CommandOperationEventCursor,
)
from domains.command.policy import DEFAULT_COMMAND_LEASE_SECONDS
from packages.config.constants import Command, CommandStatus
from packages.contracts.event_bus.interfaces import EventEnvelope, JsonObject
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.interfaces import CommandRecord
from packages.contracts.parity import OperationEvent, OperationEventKind
from packages.events.envelope import event
from packages.runtime.command_wakeup import AGENT_COMMAND_CHANNEL, wakeup_key
from packages.storage.engine import (
    UNKNOWN_AGENT_ID,
    DatabaseConnection,
    row_dict,
    serialize_command,
)
from packages.storage.schema import EventModel, OutboxModel

# 만료 명령 janitor 기준 — lease 만료 직후는 재리스 후보(lease_agent_command)라
# 건드리지 않고, 이 유예가 지나도록 어떤 에이전트도 집지 않은 명령만 소진으로 간주함.
EXPIRED_COMMAND_GRACE_SECONDS = 300
EXPIRED_COMMAND_FAILURE_MESSAGE = "command lease expired; no agent completed the command"
QUEUED_COMMAND_TTL_SECONDS = 1800
QUEUED_COMMAND_FAILURE_MESSAGE = "command queue expired; no connected agent accepted the command"
CANCELLING_COMMAND_FAILURE_MESSAGE = (
    "command lease expired during cancellation; agent did not confirm"
)
COMMAND_PRIORITY_HIGH = 100


@dataclass(frozen=True)
class CompletedAgentCommand:
    """One database transaction's workflow event and browser operation event."""

    event: EventEnvelope
    operation_event: OperationEvent | None

    @property
    def event_id(self) -> str:
        return self.event.event_id


@dataclass(frozen=True)
class HeartbeatAgentCommand:
    """Authoritative heartbeat reply, including a reverse cancel control."""

    correlation_id: str
    cancel_requested: bool
    cancel_generation: int | None
    operation_event: OperationEvent | None = None


@dataclass(frozen=True)
class LeasedAgentCommand:
    command: CommandRecord
    operation_event: OperationEvent | None


@dataclass(frozen=True)
class StartedAgentCommand:
    correlation_id: str
    operation_event: OperationEvent | None


@dataclass(frozen=True)
class StagedCommandControl:
    """Committed alongside a control event/outbox record before broker announce."""

    control_id: str
    command_id: str
    action: str
    correlation_id: str
    status: str
    attempt_id: str | None
    operation_event: OperationEvent | None


class CommandControlError(RuntimeError):
    """Base for a rejected control transition inside the event UoW."""


class CommandControlNotFound(CommandControlError):
    pass


class CommandControlConflict(CommandControlError):
    pass


class DuplicateCommandControl(CommandControlError):
    def __init__(self, control: JsonObject) -> None:
        super().__init__("command control idempotency key was already accepted")
        self.control = control


class AgentCommandCapacityExceeded(RuntimeError):
    """A transaction-scoped command capacity gate rejected a new logical row."""


def rca_test_guard_lock_key(
    workspace_id: str,
    cluster_id: str,
    resource_kind: str,
    namespace: str,
    resource_name: str,
) -> int:
    """동일 테스트 대상만 직렬화하는 PostgreSQL signed bigint advisory key."""
    canonical = "\x1f".join(
        (workspace_id, cluster_id, resource_kind.casefold(), namespace, resource_name)
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def command_capacity_lock_key(workspace_id: str, cluster_id: str, action: str) -> int:
    canonical = "\x1f".join((workspace_id, cluster_id, action))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def agent_command_values(
    *,
    correlation_id: str,
    plan: JsonObject,
    status: str,
    confirmation_event_id: str | None = None,
) -> dict[str, Any]:
    workspace_id = str(plan.get("workspace_id", DEFAULT_WORKSPACE_ID))
    cluster_id = str(plan["cluster_id"])
    priority = int(plan.get("priority") or COMMAND_PRIORITY_HIGH)
    command_payload = plan.get("payload")
    payload_body = dict(command_payload) if isinstance(command_payload, dict) else {}
    namespace = str(plan.get("namespace") or payload_body.get("namespace") or "")
    return {
        "command_id": plan["command_id"],
        "workspace_id": workspace_id,
        "correlation_id": correlation_id,
        "cluster_id": cluster_id,
        "action": plan["action"],
        "priority": priority,
        "payload": plan,
        "status": status,
        "lease_id": None,
        "agent_id": None,
        "leased_until": None,
        "started_at": None,
        "completed_at": None,
        "result": {},
        "confirmation_event_id": confirmation_event_id,
        "impact_identity": command_impact_identity(
            cluster_id=cluster_id,
            action=str(plan["action"]),
            namespace=namespace,
            diff=dict(plan.get("diff") or {}),
            payload=payload_body,
        ),
        "direct_execution": bool(plan.get("direct_execution", False)),
        "attempt_count": 0,
        "active_attempt_id": None,
        "cancel_requested_at": None,
        "cancel_requested_by": None,
        "cancel_reason": None,
        "cancel_accepted_at": None,
        "cancel_generation": 0,
        "terminal_event_id": None,
        "updated_at": func.now(),
    }


def leased_agent_command_columns(table: Any) -> tuple[Any, ...]:
    """Columns that cross the management-to-agent lease boundary."""

    return (
        table.c.command_id,
        table.c.workspace_id,
        table.c.correlation_id,
        table.c.cluster_id,
        table.c.action,
        table.c.payload,
        table.c.status,
        table.c.lease_id,
        table.c.agent_id,
        table.c.leased_until,
        table.c.active_attempt_id.label("attempt_id"),
        # Direct execution is immutable server-side authority stored beside
        # the command. The target agent must receive this column at lease
        # time; the nested plan is payload, not an execution grant.
        table.c.direct_execution,
    )


def agent_command_insert(
    *,
    correlation_id: str,
    plan: JsonObject,
    status: str,
    confirmation_event_id: str | None = None,
) -> Any:
    table = AgentCommand.__table__
    return (
        pg_insert(table)
        .values(
            **agent_command_values(
                correlation_id=correlation_id,
                plan=plan,
                status=status,
                confirmation_event_id=confirmation_event_id,
            )
        )
        .on_conflict_do_nothing(index_elements=[table.c.command_id])
    )


def agent_command_queue_upsert(
    *,
    correlation_id: str,
    plan: JsonObject,
    status: str,
    attempt_id: str,
) -> Any:
    """Project a worker plan exactly once, including its first execution attempt.

    The API receipt may already have inserted the logical row.  The conflict
    update intentionally succeeds only for that unprojected (queued/no active
    attempt) state, so duplicate worker delivery cannot create another attempt.
    """

    table = AgentCommand.__table__
    values = agent_command_values(correlation_id=correlation_id, plan=plan, status=status)
    values.update(attempt_count=1, active_attempt_id=attempt_id)
    insertion = pg_insert(table).values(**values)
    return insertion.on_conflict_do_update(
        index_elements=[table.c.command_id],
        set_={
            "attempt_count": 1,
            "active_attempt_id": attempt_id,
            "updated_at": func.now(),
        },
        where=and_(
            table.c.status == CommandStatus.QUEUED,
            table.c.active_attempt_id.is_(None),
            table.c.attempt_count == 0,
        ),
    ).returning(table.c.command_id)


def agent_command_attempt_insert(
    *,
    attempt_id: str,
    command_id: str,
    workspace_id: str,
    cluster_id: str,
    attempt_no: int,
    status: str = CommandStatus.QUEUED,
    available_at: Any | None = None,
) -> Any:
    table = AgentCommandAttempt.__table__
    values: dict[str, Any] = {
        "attempt_id": attempt_id,
        "command_id": command_id,
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "attempt_no": attempt_no,
        "status": status,
        "lease_id": None,
        "agent_id": None,
        "leased_until": None,
        "started_at": None,
        "completed_at": None,
        "result": {},
        "updated_at": func.now(),
    }
    if available_at is not None:
        values["available_at"] = available_at
    return (
        pg_insert(table)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[table.c.attempt_id])
    )


def stage_logical_command_acceptance_in_transaction(
    conn: Any,
    *,
    correlation_id: str,
    plan: JsonObject,
    confirmation_event_id: str,
    status: str = CommandStatus.QUEUED,
    max_active_per_action: int | None = None,
) -> bool:
    """Create the logical command inside the receipt event/outbox transaction.

    A browser may cancel immediately after receiving the receipt, before the
    asynchronous command worker runs.  Persisting this row here makes that
    cancellation authoritative instead of racing a missing queue projection.
    """

    table = AgentCommand.__table__
    if max_active_per_action is not None:
        if max_active_per_action < 1:
            raise ValueError("command capacity must be positive")
        workspace_id = str(plan.get("workspace_id", DEFAULT_WORKSPACE_ID))
        cluster_id = str(plan["cluster_id"])
        action = str(plan["action"])
        conn.execute(
            text("select pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": command_capacity_lock_key(workspace_id, cluster_id, action)},
        )
        active = conn.execute(
            select(func.count())
            .select_from(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.action == action,
                table.c.status.in_(
                    (
                        CommandStatus.QUEUED,
                        CommandStatus.LEASED,
                        CommandStatus.RUNNING,
                        CommandStatus.CANCEL_REQUESTED,
                        CommandStatus.CANCELLING,
                    )
                ),
            )
        ).scalar_one()
        if int(active) >= max_active_per_action:
            raise AgentCommandCapacityExceeded(f"active command capacity exceeded for {action}")
    inserted = conn.execute(
        agent_command_insert(
            correlation_id=correlation_id,
            plan=plan,
            status=status,
            confirmation_event_id=confirmation_event_id,
        ).returning(table.c.command_id)
    ).scalar_one_or_none()
    return inserted is not None


def notify_agent_command(conn: Any, workspace_id: str, cluster_id: str) -> None:
    # 트랜잭션 커밋 시 전달되어 롱폴이 즉시 lease를 재시도한다.
    conn.execute(
        text("select pg_notify(:channel, :payload)"),
        {
            "channel": AGENT_COMMAND_CHANNEL,
            "payload": wakeup_key(workspace_id, cluster_id),
        },
    )


async def append_command_operation_event_in_transaction(
    conn: Any,
    *,
    workspace_id: str,
    command_id: str,
    kind: OperationEventKind,
    payload: JsonObject,
) -> OperationEvent | None:
    """Stage one immutable operation fact in the caller's command transaction."""
    cluster_id = str(payload.get("cluster_id", "")).strip()
    if not workspace_id or not command_id or not cluster_id:
        raise ValueError("operation events require workspace, command, and cluster identity")
    cursor = CommandOperationEventCursor.__table__
    event_table = CommandOperationEvent.__table__
    terminal = kind in {"completed", "failed", "cancelled"}
    await conn.execute(
        pg_insert(cursor)
        .values(
            workspace_id=workspace_id,
            command_id=command_id,
            last_sequence=0,
            terminal_sequence=None,
        )
        .on_conflict_do_nothing(index_elements=[cursor.c.workspace_id, cursor.c.command_id])
    )
    values: dict[str, Any] = {
        "last_sequence": cursor.c.last_sequence + 1,
        "updated_at": func.now(),
    }
    if terminal:
        values["terminal_sequence"] = cursor.c.last_sequence + 1
    advanced = (
        await conn.execute(
            update(cursor)
            .where(
                cursor.c.workspace_id == workspace_id,
                cursor.c.command_id == command_id,
                cursor.c.terminal_sequence.is_(None),
            )
            .values(**values)
            .returning(cursor.c.last_sequence)
        )
    ).scalar_one_or_none()
    if advanced is None:
        return None
    row = (
        (
            await conn.execute(
                pg_insert(event_table)
                .values(
                    workspace_id=workspace_id,
                    command_id=command_id,
                    sequence=int(advanced),
                    cluster_id=cluster_id,
                    kind=kind,
                    payload=dict(payload),
                )
                .returning(
                    event_table.c.command_id,
                    event_table.c.sequence,
                    event_table.c.kind,
                    event_table.c.payload,
                    event_table.c.occurred_at,
                )
            )
        )
        .mappings()
        .one()
    )
    return OperationEvent(
        command_id=str(row["command_id"]),
        sequence=int(row["sequence"]),
        kind=str(row["kind"]),
        payload=dict(row["payload"]),
        occurred_at=row["occurred_at"],
    )


def stage_command_operation_event_in_transaction(
    conn: Any,
    *,
    workspace_id: str,
    command_id: str,
    kind: OperationEventKind,
    payload: JsonObject,
) -> OperationEvent | None:
    """Sync UoW companion used by the API event outbox receipt transaction."""
    cluster_id = str(payload.get("cluster_id", "")).strip()
    if not workspace_id or not command_id or not cluster_id:
        raise ValueError("operation events require workspace, command, and cluster identity")
    cursor = CommandOperationEventCursor.__table__
    event_table = CommandOperationEvent.__table__
    terminal = kind in {"completed", "failed", "cancelled"}
    conn.execute(
        pg_insert(cursor)
        .values(
            workspace_id=workspace_id,
            command_id=command_id,
            last_sequence=0,
            terminal_sequence=None,
        )
        .on_conflict_do_nothing(index_elements=[cursor.c.workspace_id, cursor.c.command_id])
    )
    values: dict[str, Any] = {
        "last_sequence": cursor.c.last_sequence + 1,
        "updated_at": func.now(),
    }
    if terminal:
        values["terminal_sequence"] = cursor.c.last_sequence + 1
    advanced = conn.execute(
        update(cursor)
        .where(
            cursor.c.workspace_id == workspace_id,
            cursor.c.command_id == command_id,
            cursor.c.terminal_sequence.is_(None),
        )
        .values(**values)
        .returning(cursor.c.last_sequence)
    ).scalar_one_or_none()
    if advanced is None:
        return None
    row = (
        conn.execute(
            pg_insert(event_table)
            .values(
                workspace_id=workspace_id,
                command_id=command_id,
                sequence=int(advanced),
                cluster_id=cluster_id,
                kind=kind,
                payload=dict(payload),
            )
            .returning(
                event_table.c.command_id,
                event_table.c.sequence,
                event_table.c.kind,
                event_table.c.payload,
                event_table.c.occurred_at,
            )
        )
        .mappings()
        .one()
    )
    return OperationEvent(
        command_id=str(row["command_id"]),
        sequence=int(row["sequence"]),
        kind=str(row["kind"]),
        payload=dict(row["payload"]),
        occurred_at=row["occurred_at"],
    )


def _command_retry_policy(payload: JsonObject) -> tuple[int, int]:
    policy = payload.get("retry_policy")
    if not isinstance(policy, dict):
        return 1, 0
    try:
        max_attempts = max(1, int(policy.get("max_attempts", 1)))
        delay_seconds = max(0, int(policy.get("retry_delay_seconds", 0)))
    except (TypeError, ValueError):
        return 1, 0
    return max_attempts, delay_seconds


def _current_control_row(
    conn: Any, *, workspace_id: str, command_id: str, action: str, idempotency_key: str
) -> JsonObject | None:
    controls = CommandControlAction.__table__
    row = (
        conn.execute(
            select(controls).where(
                controls.c.workspace_id == workspace_id,
                controls.c.command_id == command_id,
                controls.c.action == action,
                controls.c.idempotency_key == idempotency_key,
            )
        )
        .mappings()
        .first()
    )
    return row_dict(row) if row else None


def stage_command_control_in_transaction(
    conn: Any,
    *,
    workspace_id: str,
    command_id: str,
    action: str,
    idempotency_key: str,
    requested_by: str,
    reason: str | None,
    event_id: str,
    audit_event_id: str,
) -> StagedCommandControl:
    """Atomically persist one control intent, state fact and browser event.

    The caller is the API event outbox UoW.  A command-row lock serializes
    controls for the same immutable logical command, and duplicate keys raise
    before the event transaction can commit a second audit fact.
    """

    if action not in {"cancel", "retry"}:
        raise ValueError(f"unsupported command control action: {action}")
    commands = AgentCommand.__table__
    attempts = AgentCommandAttempt.__table__
    controls = CommandControlAction.__table__
    logical = (
        conn.execute(
            select(commands)
            .where(commands.c.workspace_id == workspace_id, commands.c.command_id == command_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if logical is None:
        raise CommandControlNotFound("command not found")
    row = row_dict(logical)
    duplicate = _current_control_row(
        conn,
        workspace_id=workspace_id,
        command_id=command_id,
        action=action,
        idempotency_key=idempotency_key,
    )
    if duplicate is not None:
        raise DuplicateCommandControl(duplicate)

    status = str(row["status"])
    cluster_id = str(row["cluster_id"])
    correlation_id = str(row["correlation_id"])
    active_attempt_id = (
        str(row["active_attempt_id"]) if row.get("active_attempt_id") is not None else None
    )
    control_id = f"ctl-{uuid.uuid4()}"
    operation_event: OperationEvent | None = None
    outcome = "accepted"
    details: JsonObject = {"status_before": status, "correlation_id": correlation_id}

    if action == "cancel":
        spec = command_action_spec(str(row["action"]))
        if spec is None or not spec.supports_cancel:
            raise CommandControlConflict("command action does not support cancellation")
        if status in {CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.CANCELLED}:
            raise CommandControlConflict("terminal command cannot be cancelled")
        if status in {CommandStatus.CANCEL_REQUESTED, CommandStatus.CANCELLING}:
            outcome = "already_requested"
            details["cancel_generation"] = int(row.get("cancel_generation") or 0)
        elif status in {CommandStatus.QUEUED, CommandStatus.LEASED}:
            result: JsonObject = {
                "status": CommandStatus.CANCELLED,
                "applied": False,
                "message": reason or "command cancelled before execution started",
            }
            conn.execute(
                update(commands)
                .where(
                    commands.c.workspace_id == workspace_id,
                    commands.c.command_id == command_id,
                    commands.c.status == status,
                )
                .values(
                    status=CommandStatus.CANCELLED,
                    result=result,
                    completed_at=func.now(),
                    cancel_requested_at=func.now(),
                    cancel_requested_by=requested_by,
                    cancel_reason=reason,
                    terminal_event_id=event_id,
                    updated_at=func.now(),
                )
            )
            if active_attempt_id is not None:
                conn.execute(
                    update(attempts)
                    .where(
                        attempts.c.attempt_id == active_attempt_id,
                        attempts.c.workspace_id == workspace_id,
                        attempts.c.status.in_([CommandStatus.QUEUED, CommandStatus.LEASED]),
                    )
                    .values(
                        status=CommandStatus.CANCELLED,
                        result=result,
                        completed_at=func.now(),
                        updated_at=func.now(),
                    )
                )
            operation_event = stage_command_operation_event_in_transaction(
                conn,
                workspace_id=workspace_id,
                command_id=command_id,
                kind="cancelled",
                payload={
                    "cluster_id": cluster_id,
                    "status": CommandStatus.CANCELLED,
                    "correlation_id": correlation_id,
                    "control_id": control_id,
                    "result": result,
                },
            )
            status = CommandStatus.CANCELLED
        else:
            generation = int(row.get("cancel_generation") or 0) + 1
            conn.execute(
                update(commands)
                .where(
                    commands.c.workspace_id == workspace_id,
                    commands.c.command_id == command_id,
                    commands.c.status == CommandStatus.RUNNING,
                )
                .values(
                    status=CommandStatus.CANCEL_REQUESTED,
                    cancel_requested_at=func.now(),
                    cancel_requested_by=requested_by,
                    cancel_reason=reason,
                    cancel_generation=generation,
                    updated_at=func.now(),
                )
            )
            operation_event = stage_command_operation_event_in_transaction(
                conn,
                workspace_id=workspace_id,
                command_id=command_id,
                kind="progress",
                payload={
                    "cluster_id": cluster_id,
                    "status": CommandStatus.CANCEL_REQUESTED,
                    "correlation_id": correlation_id,
                    "control_id": control_id,
                    "cancel_generation": generation,
                },
            )
            status = CommandStatus.CANCEL_REQUESTED
            details["cancel_generation"] = generation
    else:
        if status != CommandStatus.FAILED:
            raise CommandControlConflict("only a failed command may be retried")
        payload = dict(row.get("payload") or {})
        spec = command_action_spec(str(row["action"]))
        max_attempts, retry_delay_seconds = _command_retry_policy(payload)
        if spec is None or not spec.supports_manual_retry or max_attempts < 2:
            raise CommandControlConflict("command action retry policy does not allow manual retry")
        if int(row.get("attempt_count") or 0) >= max_attempts:
            raise CommandControlConflict("command retry budget is exhausted")
        previous_result = dict(row.get("result") or {})
        if previous_result.get("retryable") is not True:
            raise CommandControlConflict("failed command is not declared retryable")
        next_attempt_no = int(row.get("attempt_count") or 0) + 1
        active_attempt_id = f"attempt-{uuid.uuid4()}"
        available_at = func.now() + text(f"interval '{retry_delay_seconds} seconds'")
        conn.execute(
            agent_command_attempt_insert(
                attempt_id=active_attempt_id,
                command_id=command_id,
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                attempt_no=next_attempt_no,
                available_at=available_at,
            )
        )
        conn.execute(
            update(commands)
            .where(
                commands.c.workspace_id == workspace_id,
                commands.c.command_id == command_id,
                commands.c.status == CommandStatus.FAILED,
            )
            .values(
                status=CommandStatus.QUEUED,
                attempt_count=next_attempt_no,
                active_attempt_id=active_attempt_id,
                lease_id=None,
                agent_id=None,
                leased_until=None,
                started_at=None,
                completed_at=None,
                result={},
                cancel_requested_at=None,
                cancel_requested_by=None,
                cancel_reason=None,
                cancel_accepted_at=None,
                terminal_event_id=None,
                updated_at=func.now(),
            )
        )
        notify_agent_command(conn, workspace_id, cluster_id)
        operation_event = stage_command_operation_event_in_transaction(
            conn,
            workspace_id=workspace_id,
            command_id=command_id,
            kind="progress",
            payload={
                "cluster_id": cluster_id,
                "status": CommandStatus.QUEUED,
                "correlation_id": correlation_id,
                "control_id": control_id,
                "attempt_id": active_attempt_id,
                "attempt_no": next_attempt_no,
                "available_after_seconds": retry_delay_seconds,
            },
        )
        status = CommandStatus.QUEUED
        details.update(
            attempt_no=next_attempt_no,
            retry_delay_seconds=retry_delay_seconds,
            max_attempts=max_attempts,
        )

    details["status_after"] = status
    conn.execute(
        pg_insert(controls)
        .values(
            control_id=control_id,
            workspace_id=workspace_id,
            command_id=command_id,
            action=action,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            reason=reason,
            outcome=outcome,
            event_id=event_id,
            audit_event_id=audit_event_id,
            attempt_id=active_attempt_id,
            details=details,
            updated_at=func.now(),
        )
        .on_conflict_do_nothing(constraint="uq_command_control_action_idempotency")
    )
    return StagedCommandControl(
        control_id=control_id,
        command_id=command_id,
        action=action,
        correlation_id=correlation_id,
        status=status,
        attempt_id=active_attempt_id,
        operation_event=operation_event,
    )


class AgentCommandRepository(DatabaseConnection):
    def stage_integration_operation_event(
        self,
        conn: Any,
        *,
        workspace_id: str,
        operation_id: str,
        cluster_id: str,
        payload: JsonObject,
        kind: OperationEventKind = "progress",
    ) -> OperationEvent | None:
        """Stage a non-command configuration operation in the shared durable stream."""

        return stage_command_operation_event_in_transaction(
            conn,
            workspace_id=workspace_id,
            command_id=operation_id,
            kind=kind,
            payload={"cluster_id": cluster_id, **dict(payload)},
        )

    async def fail_logical_command_and_stage_event(
        self,
        workspace_id: str,
        command_id: str,
        cluster_id: str,
        reason: str,
    ) -> OperationEvent | None:
        """Close a receipt that the policy worker rejected before agent execution."""

        table = AgentCommand.__table__
        result: JsonObject = {
            "status": CommandStatus.FAILED,
            "applied": False,
            "message": reason,
            "retryable": False,
        }
        async with self.async_engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        update(table)
                        .where(
                            table.c.workspace_id == workspace_id,
                            table.c.command_id == command_id,
                            table.c.cluster_id == cluster_id,
                            table.c.status == CommandStatus.QUEUED,
                        )
                        .values(
                            status=CommandStatus.FAILED,
                            result=result,
                            completed_at=func.now(),
                            updated_at=func.now(),
                        )
                        .returning(table.c.correlation_id)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            return await append_command_operation_event_in_transaction(
                conn,
                workspace_id=workspace_id,
                command_id=command_id,
                kind="failed",
                payload={
                    "cluster_id": cluster_id,
                    "status": CommandStatus.FAILED,
                    "correlation_id": str(row["correlation_id"]),
                    "result": result,
                },
            )

    async def append_command_operation_event(
        self,
        workspace_id: str,
        command_id: str,
        kind: OperationEventKind,
        payload: JsonObject,
    ) -> OperationEvent | None:
        """Append one ordered event after its durable command transition commits.

        A cursor row is atomically advanced under PostgreSQL row locking.  Terminal
        events close the cursor, so duplicate agent completion reports never create
        a second terminal SSE message.  The returned row is committed before a
        broker is allowed to announce it.
        """
        normalized_workspace = workspace_id.strip()
        normalized_command = command_id.strip()
        cluster_id = str(payload.get("cluster_id", "")).strip()
        if not normalized_workspace or not normalized_command or not cluster_id:
            raise ValueError("operation events require workspace, command, and cluster identity")

        async with self.async_engine.begin() as conn:
            return await append_command_operation_event_in_transaction(
                conn,
                workspace_id=normalized_workspace,
                command_id=normalized_command,
                kind=kind,
                payload={"cluster_id": cluster_id, **dict(payload)},
            )

    async def list_command_operation_events(
        self,
        workspace_id: str,
        command_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[OperationEvent]:
        """Read a bounded, strictly ordered durable event suffix for SSE replay."""
        if after_sequence < 0:
            raise ValueError("operation event cursor must be non-negative")
        table = CommandOperationEvent.__table__
        statement = (
            select(
                table.c.command_id,
                table.c.sequence,
                table.c.kind,
                table.c.payload,
                table.c.occurred_at,
            )
            .where(
                table.c.workspace_id == workspace_id,
                table.c.command_id == command_id,
                table.c.sequence > after_sequence,
            )
            .order_by(table.c.sequence.asc())
            .limit(max(1, min(limit, 1000)))
        )
        async with self.async_connection() as conn:
            rows = (await conn.execute(statement)).mappings().all()
        return [
            OperationEvent(
                command_id=str(row["command_id"]),
                sequence=int(row["sequence"]),
                kind=str(row["kind"]),
                payload=dict(row["payload"]),
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]

    async def get_command_operation_event_context(
        self, workspace_id: str, command_id: str
    ) -> JsonObject | None:
        """Return durable stream authority even before ``agent_commands`` is projected.

        The receipt event is intentionally written before the asynchronous command
        projection.  A browser reconnecting from that receipt must therefore
        authorize against the immutable operation stream instead of treating the
        still-pending projection as a missing command.
        """
        cursor = CommandOperationEventCursor.__table__
        events = CommandOperationEvent.__table__
        latest_cluster = (
            select(events.c.cluster_id)
            .where(
                events.c.workspace_id == workspace_id,
                events.c.command_id == command_id,
            )
            .order_by(events.c.sequence.desc())
            .limit(1)
            .scalar_subquery()
        )
        statement = select(
            cursor.c.last_sequence,
            cursor.c.terminal_sequence,
            latest_cluster.label("cluster_id"),
        ).where(
            cursor.c.workspace_id == workspace_id,
            cursor.c.command_id == command_id,
        )
        async with self.async_connection() as conn:
            row = (await conn.execute(statement)).mappings().first()
        return row_dict(row) if row else None

    def queue_agent_command(self, correlation_id: str, plan: JsonObject, status: str) -> bool:
        if plan.get("action") == Command.RCA_TEST_SCENARIO_INJECT_ACTION:
            raise ValueError("RCA test inject commands require the atomic reservation guard")
        workspace_id = str(plan.get("workspace_id", DEFAULT_WORKSPACE_ID))
        cluster_id = str(plan["cluster_id"])
        attempt_id = f"attempt-{uuid.uuid4()}"
        with self.connection() as conn:
            inserted = conn.execute(
                agent_command_queue_upsert(
                    correlation_id=correlation_id,
                    plan=plan,
                    status=status,
                    attempt_id=attempt_id,
                )
            ).scalar_one_or_none()
            if inserted is None:
                return False
            conn.execute(
                agent_command_attempt_insert(
                    attempt_id=attempt_id,
                    command_id=str(plan["command_id"]),
                    workspace_id=workspace_id,
                    cluster_id=cluster_id,
                    attempt_no=1,
                )
            )
            notify_agent_command(conn, workspace_id, cluster_id)
            return True

    def queue_rca_test_command_if_available(
        self,
        correlation_id: str,
        plan: JsonObject,
        status: str,
        *,
        resource_kind: str,
        namespace: str,
        resource_name: str,
        max_concurrent_runs: int,
        ttl_seconds: int,
    ) -> bool:
        """같은 fixture 예약 확인과 inject enqueue를 한 DB 트랜잭션으로 처리한다."""
        if plan.get("action") != Command.RCA_TEST_SCENARIO_INJECT_ACTION:
            raise ValueError("atomic RCA test reservation accepts inject commands only")
        if max_concurrent_runs < 1 or ttl_seconds < 1:
            raise ValueError("RCA test concurrency and TTL must be positive")

        table = AgentCommand.__table__
        cleanup = table.alias("finished_rca_test_cleanup")
        workspace_id = str(plan.get("workspace_id", DEFAULT_WORKSPACE_ID))
        cluster_id = str(plan["cluster_id"])
        normalized_resource_kind = resource_kind.strip().casefold()
        inject_payload = table.c.payload["payload"]
        cleanup_payload = cleanup.c.payload["payload"]
        cleanup_finished = (
            select(1)
            .select_from(cleanup)
            .where(
                cleanup.c.workspace_id == table.c.workspace_id,
                cleanup.c.cluster_id == table.c.cluster_id,
                cleanup.c.action == Command.RCA_TEST_SCENARIO_CLEANUP_ACTION,
                cleanup.c.status == CommandStatus.COMPLETED,
                cleanup_payload["run_id"].astext == inject_payload["run_id"].astext,
            )
            .correlate(table)
            .exists()
        )
        active_count = (
            select(func.count())
            .select_from(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.action == Command.RCA_TEST_SCENARIO_INJECT_ACTION,
                func.lower(func.coalesce(inject_payload["resource_kind"].astext, "Deployment"))
                == normalized_resource_kind,
                inject_payload["namespace"].astext == namespace,
                inject_payload["resource_name"].astext == resource_name,
                cast(inject_payload["expires_at"].astext, DateTime(timezone=True)) > func.now(),
                ~cleanup_finished,
            )
        )
        lock_key = rca_test_guard_lock_key(
            workspace_id,
            cluster_id,
            resource_kind,
            namespace,
            resource_name,
        )

        with self.connection() as conn:
            conn.execute(text("select pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
            if int(conn.execute(active_count).scalar_one()) >= max_concurrent_runs:
                return False
            attempt_id = f"attempt-{uuid.uuid4()}"
            inserted = conn.execute(
                agent_command_queue_upsert(
                    correlation_id=correlation_id,
                    plan=plan,
                    status=status,
                    attempt_id=attempt_id,
                )
            ).scalar_one_or_none()
            if inserted is None:
                return False
            conn.execute(
                agent_command_attempt_insert(
                    attempt_id=attempt_id,
                    command_id=str(plan["command_id"]),
                    workspace_id=workspace_id,
                    cluster_id=cluster_id,
                    attempt_no=1,
                )
            )
            notify_agent_command(conn, workspace_id, cluster_id)
            return True

    async def get_agent_command(
        self, command_id: str, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> JsonObject | None:
        """워크스페이스 범위 명령 단건 조회 — 콘솔이 상태·실제 결과를 폴링하는 용도."""
        table = AgentCommand.__table__
        statement = select(
            table.c.command_id,
            table.c.cluster_id,
            table.c.correlation_id,
            table.c.action,
            table.c.payload,
            table.c.status,
            table.c.result,
            table.c.completed_at,
            table.c.terminal_event_id,
            table.c.confirmation_event_id,
            table.c.impact_identity,
            table.c.direct_execution,
            table.c.attempt_count,
            table.c.active_attempt_id,
            table.c.cancel_generation,
            table.c.cancel_requested_at,
            table.c.cancel_accepted_at,
        ).where(
            table.c.command_id == command_id,
            table.c.workspace_id == workspace_id,
        )
        async with self.async_connection() as conn:
            row = (await conn.execute(statement)).mappings().first()
        return row_dict(row) if row else None

    async def count_active_agent_commands(
        self,
        workspace_id: str,
        cluster_id: str,
        action: str,
    ) -> int:
        """Count bounded active commands for one workspace/cluster/action.

        The count is authoritative in PostgreSQL and includes queued work, so a
        disconnected agent cannot turn repeated browser clicks into an
        unbounded backlog.
        """

        table = AgentCommand.__table__
        statement = (
            select(func.count())
            .select_from(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.action == action,
                table.c.status.in_(
                    (
                        CommandStatus.QUEUED,
                        CommandStatus.LEASED,
                        CommandStatus.RUNNING,
                        CommandStatus.CANCEL_REQUESTED,
                        CommandStatus.CANCELLING,
                    )
                ),
            )
        )
        async with self.async_connection() as conn:
            return int((await conn.execute(statement)).scalar_one())

    async def list_agent_commands_by_correlation(
        self,
        workspace_id: str,
        correlation_id: str,
        *,
        limit: int = 20,
    ) -> list[JsonObject]:
        """Read the newest bounded command batch for one workspace correlation."""
        table = AgentCommand.__table__
        statement = (
            select(
                table.c.command_id,
                table.c.cluster_id,
                table.c.correlation_id,
                table.c.action,
                table.c.payload,
                table.c.status,
                table.c.result,
                table.c.completed_at,
                table.c.created_at,
            )
            .where(
                table.c.workspace_id == workspace_id,
                table.c.correlation_id == correlation_id,
            )
            .order_by(table.c.created_at.desc(), table.c.command_id.desc())
            .limit(max(1, min(limit, 20)))
        )
        async with self.async_connection() as conn:
            rows = (await conn.execute(statement)).mappings().all()
        return [row_dict(row) for row in rows]

    async def lease_agent_command(
        self,
        cluster_id: str,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        queued_status: str = CommandStatus.QUEUED,
        leased_status: str = CommandStatus.LEASED,
        agent_id: str = UNKNOWN_AGENT_ID,
        lease_seconds: int = DEFAULT_COMMAND_LEASE_SECONDS,
    ) -> LeasedAgentCommand | None:
        table = AgentCommand.__table__
        attempts = AgentCommandAttempt.__table__
        now = datetime.now(UTC)
        leased_until = now + timedelta(seconds=lease_seconds)
        lease_id = str(uuid.uuid4())
        columns = leased_agent_command_columns(table)
        # A running lease may have applied side effects before the agent died.
        # It is deliberately never re-leased: the janitor marks it failed for a
        # human-reviewed retry instead of silently running the command twice.
        available = or_(
            (table.c.status == queued_status) & (attempts.c.status == queued_status),
            (table.c.status == leased_status)
            & (attempts.c.status == leased_status)
            & (attempts.c.leased_until < func.now()),
        )
        candidate = (
            select(table.c.command_id, attempts.c.attempt_id)
            .join(attempts, attempts.c.attempt_id == table.c.active_attempt_id)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                attempts.c.workspace_id == workspace_id,
                attempts.c.cluster_id == cluster_id,
                attempts.c.available_at <= func.now(),
                available,
            )
            .order_by(table.c.priority.desc(), table.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        async with self.async_connection() as conn:
            selected = (await conn.execute(candidate)).mappings().first()
            if selected is None:
                return None
            attempt_id = str(selected["attempt_id"])
            statement = (
                update(table)
                .where(
                    table.c.command_id == str(selected["command_id"]),
                    table.c.workspace_id == workspace_id,
                    table.c.active_attempt_id == attempt_id,
                    table.c.status.in_([queued_status, leased_status]),
                )
                .values(
                    status=leased_status,
                    lease_id=lease_id,
                    agent_id=agent_id,
                    leased_until=leased_until,
                    updated_at=func.now(),
                )
                .returning(*columns)
            )
            leased = (await conn.execute(statement)).mappings().first()
            if leased is None:
                return None
            await conn.execute(
                update(attempts)
                .where(
                    attempts.c.attempt_id == attempt_id,
                    attempts.c.workspace_id == workspace_id,
                    attempts.c.status.in_([queued_status, leased_status]),
                )
                .values(
                    status=leased_status,
                    lease_id=lease_id,
                    agent_id=agent_id,
                    leased_until=leased_until,
                    updated_at=func.now(),
                )
            )
            command = serialize_command(row_dict(leased))
            operation_event = await append_command_operation_event_in_transaction(
                conn,
                workspace_id=workspace_id,
                command_id=str(command["command_id"]),
                kind="progress",
                payload={
                    "cluster_id": cluster_id,
                    "status": leased_status,
                    "action": str(command["action"]),
                    "correlation_id": str(command["correlation_id"]),
                    "attempt_id": str(command["attempt_id"]),
                },
            )
            return LeasedAgentCommand(command=command, operation_event=operation_event)

    async def start_agent_command(
        self,
        command_id: str,
        workspace_id: str,
        cluster_id: str,
        lease_id: str,
        agent_id: str,
        running_status: str = CommandStatus.RUNNING,
        lease_seconds: int = DEFAULT_COMMAND_LEASE_SECONDS,
        attempt_id: str | None = None,
    ) -> StartedAgentCommand | None:
        table = AgentCommand.__table__
        attempts = AgentCommandAttempt.__table__
        leased_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        conditions = [
            table.c.command_id == command_id,
            table.c.workspace_id == workspace_id,
            table.c.cluster_id == cluster_id,
            table.c.lease_id == lease_id,
            table.c.agent_id == agent_id,
            table.c.status == CommandStatus.LEASED,
            table.c.leased_until >= func.now(),
        ]
        if attempt_id is not None:
            conditions.append(table.c.active_attempt_id == attempt_id)
        statement = (
            update(table)
            .where(*conditions)
            .values(
                status=running_status,
                leased_until=leased_until,
                started_at=func.now(),
                updated_at=func.now(),
            )
            .returning(table.c.correlation_id, table.c.active_attempt_id)
        )
        async with self.async_engine.begin() as conn:
            row = (await conn.execute(statement)).mappings().first()
            if row and row["active_attempt_id"]:
                await conn.execute(
                    update(attempts)
                    .where(
                        attempts.c.attempt_id == str(row["active_attempt_id"]),
                        attempts.c.workspace_id == workspace_id,
                        attempts.c.lease_id == lease_id,
                        attempts.c.agent_id == agent_id,
                        attempts.c.status == CommandStatus.LEASED,
                    )
                    .values(
                        status=running_status,
                        started_at=func.now(),
                        leased_until=leased_until,
                        updated_at=func.now(),
                    )
                )
            if row is None:
                return None
            operation_event = await append_command_operation_event_in_transaction(
                conn,
                workspace_id=workspace_id,
                command_id=command_id,
                kind="progress",
                payload={
                    "cluster_id": cluster_id,
                    "status": running_status,
                    "correlation_id": str(row["correlation_id"]),
                    "attempt_id": str(row["active_attempt_id"] or "") or None,
                },
            )
        return StartedAgentCommand(
            correlation_id=str(row["correlation_id"]), operation_event=operation_event
        )

    async def heartbeat_agent_command(
        self,
        command_id: str,
        workspace_id: str,
        cluster_id: str,
        lease_id: str,
        agent_id: str,
        lease_seconds: int = DEFAULT_COMMAND_LEASE_SECONDS,
        attempt_id: str | None = None,
        observed_cancel_generation: int | None = None,
    ) -> HeartbeatAgentCommand | None:
        table = AgentCommand.__table__
        attempts = AgentCommandAttempt.__table__
        leased_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        statement = (
            select(
                table.c.correlation_id,
                table.c.status,
                table.c.active_attempt_id,
                table.c.cancel_generation,
            )
            .where(
                table.c.command_id == command_id,
                table.c.workspace_id == workspace_id,
                table.c.cluster_id == cluster_id,
                table.c.lease_id == lease_id,
                table.c.agent_id == agent_id,
                table.c.status.in_(
                    [
                        CommandStatus.LEASED,
                        CommandStatus.RUNNING,
                        CommandStatus.CANCEL_REQUESTED,
                        CommandStatus.CANCELLING,
                    ]
                ),
                table.c.leased_until >= func.now(),
            )
            .with_for_update()
        )
        async with self.async_engine.begin() as conn:
            row = (await conn.execute(statement)).mappings().first()
            if row is None:
                return None
            active_attempt_id = str(row.get("active_attempt_id") or "")
            if attempt_id is not None and attempt_id != active_attempt_id:
                return None
            status = str(row["status"])
            generation = int(row["cancel_generation"] or 0)
            acknowledged = (
                status == CommandStatus.CANCEL_REQUESTED
                and observed_cancel_generation is not None
                and observed_cancel_generation >= generation
            )
            next_status = CommandStatus.CANCELLING if acknowledged else status
            await conn.execute(
                update(table)
                .where(
                    table.c.command_id == command_id,
                    table.c.workspace_id == workspace_id,
                    table.c.lease_id == lease_id,
                    table.c.agent_id == agent_id,
                    table.c.active_attempt_id == active_attempt_id,
                )
                .values(
                    status=next_status,
                    leased_until=leased_until,
                    cancel_accepted_at=(func.now() if acknowledged else table.c.cancel_accepted_at),
                    updated_at=func.now(),
                )
            )
            if active_attempt_id:
                await conn.execute(
                    update(attempts)
                    .where(
                        attempts.c.attempt_id == active_attempt_id,
                        attempts.c.workspace_id == workspace_id,
                        attempts.c.lease_id == lease_id,
                        attempts.c.agent_id == agent_id,
                        attempts.c.status.in_([CommandStatus.LEASED, CommandStatus.RUNNING]),
                    )
                    .values(leased_until=leased_until, updated_at=func.now())
                )
            operation_event = None
            if acknowledged:
                operation_event = await append_command_operation_event_in_transaction(
                    conn,
                    workspace_id=workspace_id,
                    command_id=command_id,
                    kind="progress",
                    payload={
                        "cluster_id": cluster_id,
                        "status": CommandStatus.CANCELLING,
                        "correlation_id": str(row["correlation_id"]),
                        "cancel_generation": generation,
                    },
                )
        return HeartbeatAgentCommand(
            correlation_id=str(row["correlation_id"]),
            cancel_requested=next_status
            in {CommandStatus.CANCEL_REQUESTED, CommandStatus.CANCELLING},
            cancel_generation=generation if generation else None,
            operation_event=operation_event,
        )

    async def complete_agent_command_and_stage_event(
        self,
        command_id: str,
        workspace_id: str,
        cluster_id: str,
        result: JsonObject,
        lease_id: str,
        agent_id: str,
        source: str,
        attempt_id: str | None = None,
    ) -> CompletedAgentCommand | None:
        command_table = AgentCommand.__table__
        attempts = AgentCommandAttempt.__table__
        event_table = EventModel.__table__
        outbox_table = OutboxModel.__table__
        conditions = [
            command_table.c.command_id == command_id,
            command_table.c.workspace_id == workspace_id,
            command_table.c.cluster_id == cluster_id,
            command_table.c.lease_id == lease_id,
            command_table.c.agent_id == agent_id,
            command_table.c.status.in_(
                [
                    CommandStatus.RUNNING,
                    CommandStatus.CANCEL_REQUESTED,
                    CommandStatus.CANCELLING,
                ]
            ),
            command_table.c.leased_until >= func.now(),
        ]
        if attempt_id is not None:
            conditions.append(command_table.c.active_attempt_id == attempt_id)
        statement = (
            update(command_table)
            .where(*conditions)
            .values(
                status=result["status"],
                result=result,
                completed_at=func.now(),
                updated_at=func.now(),
            )
            .returning(
                command_table.c.correlation_id,
                command_table.c.active_attempt_id,
                command_table.c.attempt_count,
                command_table.c.payload,
            )
        )
        async with self.async_engine.begin() as conn:
            row = (await conn.execute(statement)).mappings().first()
            if not row:
                return None
            active_attempt_id = str(row.get("active_attempt_id") or "")
            if active_attempt_id:
                await conn.execute(
                    update(attempts)
                    .where(
                        attempts.c.attempt_id == active_attempt_id,
                        attempts.c.workspace_id == workspace_id,
                        attempts.c.lease_id == lease_id,
                        attempts.c.agent_id == agent_id,
                        attempts.c.status.in_([CommandStatus.RUNNING, CommandStatus.LEASED]),
                    )
                    .values(
                        status=result["status"],
                        result=result,
                        completed_at=func.now(),
                        updated_at=func.now(),
                    )
                )

            final_failure = True
            if result["status"] == CommandStatus.FAILED:
                payload = dict(row.get("payload") or {})
                spec = command_action_spec(str(payload.get("action") or ""))
                max_attempts, _ = _command_retry_policy(payload)
                final_failure = not (
                    result.get("retryable") is True
                    and spec is not None
                    and spec.supports_manual_retry
                    and int(row.get("attempt_count") or 0) < max_attempts
                )
            operation_kind: OperationEventKind = command_terminal_event_kind(
                str(result["status"]),
                final=(str(result["status"]) != CommandStatus.FAILED or final_failure),
            )
            operation_event = await append_command_operation_event_in_transaction(
                conn,
                workspace_id=workspace_id,
                command_id=command_id,
                kind=operation_kind,
                payload={
                    "cluster_id": cluster_id,
                    "status": str(result["status"]),
                    "correlation_id": str(row["correlation_id"]),
                    "result": result,
                    "attempt_id": active_attempt_id or None,
                    "terminal": operation_kind != "progress",
                },
            )

            body = CommandCompletedBody(command_id=command_id, result=result)
            completed = event(
                body.__subject__,
                source,
                body.to_body(),
                str(row["correlation_id"]),
                workspace_id=workspace_id,
            )
            if operation_kind != "progress":
                await conn.execute(
                    update(command_table)
                    .where(
                        command_table.c.command_id == command_id,
                        command_table.c.workspace_id == workspace_id,
                    )
                    .values(terminal_event_id=completed.event_id)
                )
            await conn.execute(
                pg_insert(event_table)
                .values(
                    event_id=completed.event_id,
                    subject=completed.subject,
                    source=completed.source,
                    correlation_id=completed.correlation_id,
                    causation_id=completed.causation_id,
                    payload=completed.payload,
                )
                .on_conflict_do_nothing(index_elements=[event_table.c.event_id])
            )
            await conn.execute(
                pg_insert(outbox_table)
                .values(
                    event_id=completed.event_id,
                    subject=completed.subject,
                    source=completed.source,
                    correlation_id=completed.correlation_id,
                    causation_id=completed.causation_id,
                    workspace_id=completed.workspace_id,
                    occurred_at=completed.created_at,
                    payload=completed.payload,
                )
                .on_conflict_do_nothing(index_elements=[outbox_table.c.event_id])
            )
        return CompletedAgentCommand(event=completed, operation_event=operation_event)

    def fail_expired_agent_commands(
        self,
        grace_seconds: int = EXPIRED_COMMAND_GRACE_SECONDS,
        *,
        queue_ttl_seconds: int = QUEUED_COMMAND_TTL_SECONDS,
        source: str = "command-janitor",
    ) -> list[JsonObject]:
        """미수신 queue와 만료 lease를 종결하고 CommandCompleted를 outbox에 적재함.

        등록이 사라지거나 Agent가 연결을 잃으면 QUEUED 행도 lease 없이 영구 잔존할 수
        있다. 오래된 QUEUED와 lease 유예가 지난 LEASED/RUNNING을 단일 원자
        UPDATE ... RETURNING으로 닫는다.

        완료 이벤트는 상태 변경과 같은 트랜잭션에서 event+outbox 테이블에 적재한다
        (complete_agent_command 와 동일 패턴) — 호출자가 NATS 로 직접 발행하면
        커밋과 발행 사이 크래시로 이벤트가 영구 유실되고, 명령은 이미 terminal 이라
        다음 sweep 에서 재발견되지 않아 대기 워크플로가 영원히 멈추기 때문이다.
        event_id 는 (command_id, attempt) 기반 결정적 값이라 sweep 재실행에도 멱등이다.

        취소 절차(CANCEL_REQUESTED/CANCELLING) 중 에이전트가 죽으면 확인 응답이
        영원히 오지 않아 명령이 활성 상태로 잔존한다 — SSE 종료·동시 실행 용량·
        상위 워크플로가 모두 잠기므로, 같은 유예 기준으로 CANCELLED 종결한다
        (두 전이 모두 lifecycle 허용 전이에 이미 존재).
        """
        grace_seconds = int(grace_seconds)
        queue_ttl_seconds = int(queue_ttl_seconds)
        if grace_seconds < 1 or queue_ttl_seconds < 1:
            raise ValueError("command expiry durations must be positive")
        table = AgentCommand.__table__
        attempts = AgentCommandAttempt.__table__
        cancelling_states = table.c.status.in_(
            [CommandStatus.CANCEL_REQUESTED, CommandStatus.CANCELLING]
        )
        # RUNNING→CANCEL_REQUESTED 전이는 lease를 보존하지만, 방어적으로 lease가
        # 비어 있는 비정상 행도 updated_at 기준으로 회수 대상에 포함한다.
        cancelling_expired = func.coalesce(table.c.leased_until, table.c.updated_at) < (
            func.now() - text(f"interval '{grace_seconds} seconds'")
        )
        terminal_status = case(
            (cancelling_states, CommandStatus.CANCELLED),
            else_=CommandStatus.FAILED,
        )
        statement = (
            update(table)
            .where(
                or_(
                    (
                        (table.c.status == CommandStatus.QUEUED)
                        & (
                            table.c.created_at
                            < func.now() - text(f"interval '{queue_ttl_seconds} seconds'")
                        )
                    ),
                    (
                        table.c.status.in_([CommandStatus.LEASED, CommandStatus.RUNNING])
                        & (
                            table.c.leased_until
                            < func.now() - text(f"interval '{grace_seconds} seconds'")
                        )
                    ),
                    cancelling_states & cancelling_expired,
                )
            )
            .values(
                status=terminal_status,
                result=func.jsonb_build_object(
                    "status",
                    terminal_status,
                    "applied",
                    False,
                    "message",
                    case(
                        (
                            table.c.status == CommandStatus.QUEUED,
                            QUEUED_COMMAND_FAILURE_MESSAGE,
                        ),
                        (cancelling_states, CANCELLING_COMMAND_FAILURE_MESSAGE),
                        else_=EXPIRED_COMMAND_FAILURE_MESSAGE,
                    ),
                ),
                completed_at=func.now(),
                updated_at=func.now(),
            )
            .returning(
                table.c.command_id,
                table.c.workspace_id,
                table.c.cluster_id,
                table.c.correlation_id,
                table.c.active_attempt_id,
                table.c.attempt_count,
                table.c.status,
                table.c.result,
            )
        )
        event_table = EventModel.__table__
        outbox_table = OutboxModel.__table__
        with self.connection() as conn:
            rows = [row_dict(row) for row in conn.execute(statement).mappings().all()]
            for row in rows:
                if row.get("active_attempt_id"):
                    conn.execute(
                        update(attempts)
                        .where(
                            attempts.c.attempt_id == str(row["active_attempt_id"]),
                            attempts.c.workspace_id == str(row["workspace_id"]),
                            attempts.c.status.in_(
                                [CommandStatus.QUEUED, CommandStatus.LEASED, CommandStatus.RUNNING]
                            ),
                        )
                        .values(
                            status="expired",
                            result=dict(row["result"]),
                            completed_at=func.now(),
                            updated_at=func.now(),
                        )
                    )
                final_status = str(row["status"])
                stage_command_operation_event_in_transaction(
                    conn,
                    workspace_id=str(row["workspace_id"]),
                    command_id=str(row["command_id"]),
                    kind=(
                        "cancelled" if final_status == CommandStatus.CANCELLED else "failed"
                    ),
                    payload={
                        "cluster_id": str(row["cluster_id"]),
                        "status": final_status,
                        "correlation_id": str(row["correlation_id"]),
                        "result": dict(row["result"]),
                    },
                )
                command_id = str(row["command_id"])
                workspace_id = str(row["workspace_id"])
                body = CommandCompletedBody(command_id=command_id, result=dict(row["result"]))
                completed = replace(
                    event(
                        body.__subject__,
                        source,
                        body.to_body(),
                        str(row["correlation_id"]),
                        workspace_id=workspace_id,
                    ),
                    event_id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"command-expired:{command_id}:{int(row.get('attempt_count') or 0)}",
                        )
                    ),
                )
                conn.execute(
                    update(table)
                    .where(
                        table.c.command_id == command_id,
                        table.c.workspace_id == workspace_id,
                    )
                    .values(terminal_event_id=completed.event_id)
                )
                conn.execute(
                    pg_insert(event_table)
                    .values(
                        event_id=completed.event_id,
                        subject=completed.subject,
                        source=completed.source,
                        correlation_id=completed.correlation_id,
                        causation_id=completed.causation_id,
                        payload=completed.payload,
                    )
                    .on_conflict_do_nothing(index_elements=[event_table.c.event_id])
                )
                conn.execute(
                    pg_insert(outbox_table)
                    .values(
                        event_id=completed.event_id,
                        subject=completed.subject,
                        source=completed.source,
                        correlation_id=completed.correlation_id,
                        causation_id=completed.causation_id,
                        workspace_id=completed.workspace_id,
                        occurred_at=completed.created_at,
                        payload=completed.payload,
                    )
                    .on_conflict_do_nothing(index_elements=[outbox_table.c.event_id])
                )
        return rows

    def command_status_counts(self) -> dict[str, int]:
        table = AgentCommand.__table__
        statement = select(table.c.status, func.count().label("count")).group_by(table.c.status)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return {row["status"]: int(row["count"]) for row in rows}

    def oldest_command_age_seconds(self, status: str) -> float:
        table = AgentCommand.__table__
        statement = select(func.extract("epoch", func.now() - func.min(table.c.created_at))).where(
            table.c.status == status
        )
        with self.connection() as conn:
            age = conn.execute(statement).scalar()
        return float(age or 0)
