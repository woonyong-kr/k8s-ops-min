"""Reference-parity contracts shared by all Python feature adapters.

The product does not mirror an upstream route table.  These models are the
single canonical boundary between a dynamic cluster capability catalog, command
dispatch, and browser/desktop consumers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from packages.contracts.modeling import StrictModel

Freshness = Literal["live", "stale", "partial", "disconnected"]
CommandStatus = Literal[
    "queued",
    "leased",
    "running",
    "cancel_requested",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
]
OperationEventKind = Literal["progress", "log", "completed", "failed", "cancelled"]


class ClusterScope(StrictModel):
    workspace_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    namespaces: tuple[str, ...] = ()
    freshness: Freshness = "live"

    @field_validator("namespaces")
    @classmethod
    def canonicalize_namespaces(cls, namespaces: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({namespace.strip() for namespace in namespaces if namespace.strip()}))


class ResourceRef(StrictModel):
    api_group: str = ""
    version: str = ""
    kind: str = Field(min_length=1)
    namespace: str | None = None
    name: str = Field(min_length=1)
    uid: str = Field(min_length=1)


class CapabilitySet(StrictModel):
    scope: ClusterScope
    resource: ResourceRef
    revision: str = Field(min_length=1)
    actions: tuple[str, ...] = ()

    @field_validator("actions")
    @classmethod
    def canonicalize_actions(cls, actions: tuple[str, ...]) -> tuple[str, ...]:
        if len(actions) != len(set(actions)):
            raise ValueError("capability actions must be unique")
        values = tuple(sorted(action for action in actions if action))
        if len(values) != len(actions):
            raise ValueError("capability actions must be non-empty")
        return values


class CommandRequest(StrictModel):
    scope: ClusterScope
    resource: ResourceRef
    action: str = Field(min_length=1)
    diff: dict[str, Any] = Field(default_factory=dict)
    confirmation: Literal[True]
    reason: str = Field(min_length=1, max_length=500)


class CommandReceipt(StrictModel):
    """접수 UoW가 보장한 명령 trace receipt.

    ``event_id``/``audit_event_id``는 같은 immutable ``command.requested`` event
    ID다. audit worker가 비동기로 이 값을 ``audit_log.event_id``에 투영하므로,
    아직 존재하지 않을 수 있는 ``audit_log.id``를 receipt에 만들어 넣지 않는다.
    """

    accepted: Literal[True] = True
    command_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    status: CommandStatus
    # 이전 parity 소비자 입력을 읽기 위해서만 남긴다. HTTP receipt는 절대 채우지
    # 않으며, audit_log의 자동증가 PK를 뜻하지 않는다.
    audit_id: str | None = None

    @model_validator(mode="after")
    def audit_event_tracks_the_accepted_event(self) -> CommandReceipt:
        if self.audit_event_id != self.event_id:
            raise ValueError("audit_event_id must equal the immutable event_id")
        return self


class CommandControlReceipt(StrictModel):
    """Receipt for an idempotent cancel or retry control request."""

    accepted: Literal[True] = True
    command_id: str = Field(min_length=1)
    action: Literal["cancel", "retry"]
    event_id: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    status: CommandStatus
    idempotent: bool = False
    attempt_id: str | None = None

    @model_validator(mode="after")
    def audit_event_tracks_control_event(self) -> CommandControlReceipt:
        if self.audit_event_id != self.event_id:
            raise ValueError("audit_event_id must equal the immutable event_id")
        return self


class OperationEvent(StrictModel):
    command_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    kind: OperationEventKind
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
