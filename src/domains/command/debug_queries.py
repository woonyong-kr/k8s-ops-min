"""Shared internal boundary for persisted, agent-executed telemetry debug queries."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from domains.command.policy import (
    DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_COMMAND_LEASE_SECONDS,
    DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
    DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
)
from packages.config.constants import Command, CommandStatus, Sandbox
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.requests import AgentDebugQueryRequest

COMMAND_PRIORITY_HIGH = 100
LOG_STREAM_QUERY_NAME_PREFIX = "browser_log_stream_"
LOG_STREAM_QUERY_METADATA_KEY = "log_stream"


@dataclass(frozen=True)
class QueuedDebugQuery:
    command_id: str
    correlation_id: str
    plan: JsonObject
    inserted: bool


def is_reserved_log_stream_query(query: dict[str, Any]) -> bool:
    """Keep browser stream handles server-owned at the public debug boundary."""
    name = query.get("name")
    return LOG_STREAM_QUERY_METADATA_KEY in query or (
        isinstance(name, str) and name.startswith(LOG_STREAM_QUERY_NAME_PREFIX)
    )


def debug_query_plan(
    payload: AgentDebugQueryRequest,
    *,
    workspace_id: str,
    requested_by: str,
    correlation_id: str,
) -> JsonObject:
    plan_basis = {
        "workspace_id": workspace_id,
        "cluster_id": payload.cluster_id,
        "action": Command.TELEMETRY_QUERY_RUN_ACTION,
        "query": payload.query,
    }
    encoded = json.dumps(plan_basis, sort_keys=True, separators=(",", ":"), default=str)
    idempotency_key = hashlib.sha256(encoded.encode()).hexdigest()
    return {
        "command_id": f"cmd-debug-{idempotency_key[:24]}",
        "idempotency_key": idempotency_key,
        "cluster_id": payload.cluster_id,
        "action": Command.TELEMETRY_QUERY_RUN_ACTION,
        "namespace": Sandbox.NAMESPACE,
        "diff": {
            "resource": "telemetry/query",
            "namespace": Sandbox.NAMESPACE,
            "risk": Sandbox.RISK_TAG.value,
            "basis": {"query": payload.query},
        },
        "payload": {"query": payload.query},
        "steps": ["telemetry debug query"],
        "lease": {
            "lease_seconds": DEFAULT_COMMAND_LEASE_SECONDS,
            "heartbeat_interval_seconds": DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
        },
        "retry_policy": {
            "max_attempts": DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
            "retry_delay_seconds": DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
        },
        "routing_constraint": {
            "channel": "agent",
            "cluster_id": payload.cluster_id,
            "workspace_id": workspace_id,
            "required_capability": "collector",
        },
        "workspace_id": workspace_id,
        "priority": COMMAND_PRIORITY_HIGH,
        "requested_by": requested_by,
        "reason": payload.reason or "telemetry debug query",
        "correlation_id": correlation_id,
    }


def queue_debug_query(
    db: Any,
    payload: AgentDebugQueryRequest,
    *,
    workspace_id: str,
    requested_by: str,
    correlation_id: str | None = None,
) -> QueuedDebugQuery:
    correlation = correlation_id or f"corr-debug-{uuid.uuid4()}"
    plan = debug_query_plan(
        payload,
        workspace_id=workspace_id,
        requested_by=requested_by,
        correlation_id=correlation,
    )
    inserted = bool(db.queue_agent_command(correlation, plan, CommandStatus.QUEUED))
    return QueuedDebugQuery(
        command_id=str(plan["command_id"]),
        correlation_id=correlation,
        plan=plan,
        inserted=inserted,
    )
