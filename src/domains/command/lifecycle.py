"""Server-owned command lifecycle vocabulary and immutable impact identity.

The database repository owns locking and persistence.  This module deliberately
contains only pure transition and hashing rules so the HTTP gateway, worker and
cluster agent cannot drift into separate state machines.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal

from packages.config.constants import CommandStatus

CommandLifecycleStatus = Literal[
    "queued",
    "leased",
    "running",
    "cancel_requested",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
]
CommandAttemptStatus = Literal[
    "queued",
    "leased",
    "running",
    "completed",
    "failed",
    "cancelled",
    "expired",
]
CommandControlAction = Literal["cancel", "retry"]

TERMINAL_COMMAND_STATUSES = frozenset(
    {CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.CANCELLED}
)
ACTIVE_COMMAND_STATUSES = frozenset(
    {
        CommandStatus.QUEUED,
        CommandStatus.LEASED,
        CommandStatus.RUNNING,
        CommandStatus.CANCEL_REQUESTED,
        CommandStatus.CANCELLING,
    }
)
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    CommandStatus.QUEUED: frozenset(
        {CommandStatus.LEASED, CommandStatus.CANCELLED, CommandStatus.FAILED}
    ),
    CommandStatus.LEASED: frozenset(
        {
            CommandStatus.QUEUED,
            CommandStatus.RUNNING,
            CommandStatus.CANCEL_REQUESTED,
            CommandStatus.CANCELLED,
            CommandStatus.FAILED,
        }
    ),
    CommandStatus.RUNNING: frozenset(
        {
            CommandStatus.CANCEL_REQUESTED,
            CommandStatus.COMPLETED,
            CommandStatus.FAILED,
        }
    ),
    CommandStatus.CANCEL_REQUESTED: frozenset(
        {
            CommandStatus.CANCELLING,
            CommandStatus.CANCELLED,
            CommandStatus.COMPLETED,
            CommandStatus.FAILED,
        }
    ),
    CommandStatus.CANCELLING: frozenset(
        {CommandStatus.CANCELLED, CommandStatus.COMPLETED, CommandStatus.FAILED}
    ),
    # A manual retry creates a new attempt but keeps the immutable command ID.
    CommandStatus.FAILED: frozenset({CommandStatus.QUEUED}),
    CommandStatus.COMPLETED: frozenset(),
    CommandStatus.CANCELLED: frozenset(),
}


def command_status_is_terminal(status: str) -> bool:
    return status in TERMINAL_COMMAND_STATUSES


def command_status_is_active(status: str) -> bool:
    return status in ACTIVE_COMMAND_STATUSES


def command_transition_allowed(current: str, target: str) -> bool:
    """Return whether a logical command may move to ``target``.

    Idempotent storage operations may retain the same state; callers must
    separately validate their lease/control idempotency key before accepting
    those no-op repetitions.
    """

    return current == target or target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def command_terminal_event_kind(status: str, *, final: bool) -> str:
    """Map a state fact to the durable SSE event kind.

    A retryable attempt failure is a ``progress`` fact, not a terminal stream
    close: the same logical ``command_id`` may receive a later retry attempt.
    """

    if not final:
        return "progress"
    if status == CommandStatus.COMPLETED:
        return "completed"
    if status == CommandStatus.CANCELLED:
        return "cancelled"
    if status == CommandStatus.FAILED:
        return "failed"
    raise ValueError(f"non-terminal command status cannot close operation stream: {status}")


def command_impact_identity(
    *,
    cluster_id: str,
    action: str,
    namespace: str,
    diff: Mapping[str, object],
    payload: Mapping[str, object],
) -> str:
    """Return the immutable direct-command target/impact fingerprint.

    Direct retry is safe only when this digest is identical to the original
    accepted request.  It includes the target, requested action, inspected
    diff and action payload, but deliberately excludes mutable approvals,
    actor identity, time and leases.
    """

    canonical = {
        "action": action,
        "cluster_id": cluster_id,
        "diff": dict(diff),
        "namespace": namespace,
        "payload": dict(payload),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
