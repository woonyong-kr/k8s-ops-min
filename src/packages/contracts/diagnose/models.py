"""Strict, resource-scoped contracts for durable Diagnose investigations.

The models deliberately describe product concepts rather than an upstream
runtime.  A concrete Python adapter supplies persistence and the investigation
engine; browser and desktop consumers use these contracts without importing an
engine implementation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ClusterScope, CommandReceipt, CommandRequest, ResourceRef

DiagnoseRunStatus = Literal[
    "queued",
    "running",
    "awaiting_confirmation",
    "completed",
    "failed",
    "stopped",
    "stale",
    "unavailable",
]
DiagnoseEventKind = Literal[
    "phase",
    "turn",
    "step",
    "thinking",
    "verdict",
    "command.proposal",
    "command.receipt",
    "operation",
    "error",
    "closed",
]
DiagnoseReplayState = Literal["available", "resync_required"]
DiagnoseConsentSurface = Literal["browser", "desktop"]
DiagnoseHistoryStatus = Literal["available", "degraded"]

_TARGET_KEY_VERSION = "diagnose-target-v1"


class DiagnoseTarget(StrictModel):
    """The exact workspace, cluster, namespace scope, and Kubernetes object."""

    scope: ClusterScope
    resource: ResourceRef

    @model_validator(mode="after")
    def validate_namespace_scope(self) -> Self:
        namespaces = self.scope.namespaces
        namespace = self.resource.namespace
        if namespaces and namespace not in namespaces:
            raise ValueError("resource namespace must be included in the selected scope")
        return self


class DiagnoseAgentSelection(StrictModel):
    agent_id: str = Field(min_length=1, max_length=120)
    isolated: bool = False
    model: str | None = Field(default=None, min_length=1, max_length=200)
    effort: Literal["minimal", "low", "medium", "high"] = "medium"


class DiagnoseRunCreateRequest(StrictModel):
    target: DiagnoseTarget
    agent: DiagnoseAgentSelection


class DiagnoseResourceRunRequest(StrictModel):
    """Browser request; Python re-resolves this identity before building a target."""

    cluster_id: str = Field(min_length=1, max_length=512)
    resource_type: str = Field(min_length=1, max_length=80)
    api_group: str = Field(default="", max_length=253)
    api_version: str = Field(min_length=1, max_length=63)
    kind: str = Field(min_length=1, max_length=120)
    namespace: str | None = Field(default=None, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    uid: str = Field(min_length=1, max_length=253)
    agent: DiagnoseAgentSelection
    disclosure_revision: str = Field(min_length=1, max_length=120)


class DiagnoseConsentRequest(StrictModel):
    """Records disclosure consent only; it cannot authorize a cluster command."""

    scope: ClusterScope
    agent_id: str = Field(min_length=1, max_length=120)
    disclosure_revision: str = Field(min_length=1, max_length=120)
    surface: DiagnoseConsentSurface


class DiagnoseConsentGrant(StrictModel):
    scope: ClusterScope
    agent_id: str = Field(min_length=1, max_length=120)
    disclosure_revision: str = Field(min_length=1, max_length=120)
    surface: DiagnoseConsentSurface
    granted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiagnoseAgentAvailability(StrictModel):
    """An engine adapter's actual availability, never a synthetic diagnosis."""

    available: bool
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        if not self.available and not self.reason:
            raise ValueError("an unavailable Diagnose engine requires a reason")
        if self.available and self.reason is not None:
            raise ValueError("an available Diagnose engine must not have an unavailable reason")
        return self


class DiagnoseRun(StrictModel):
    """Durable investigation identity and lifecycle state.

    ``target_key`` ignores freshness deliberately: changing a freshness label
    does not make a different Kubernetes resource.  ``deduplication_key`` is
    additionally bound to the actor and selected engine configuration, so two
    users cannot be joined into the same transcript implicitly.
    """

    run_id: str = Field(min_length=1, max_length=160)
    target: DiagnoseTarget
    agent: DiagnoseAgentSelection
    requested_by: str = Field(min_length=1, max_length=160)
    status: DiagnoseRunStatus
    target_key: str = Field(default="", min_length=1, max_length=128)
    deduplication_key: str = Field(default="", min_length=1, max_length=128)
    status_reason: str | None = Field(default=None, min_length=1, max_length=1000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def normalize_keys_and_status(self) -> Self:
        expected_target_key = make_diagnose_target_key(self.target)
        expected_deduplication_key = make_diagnose_deduplication_key(
            target_key=expected_target_key,
            agent=self.agent,
            requested_by=self.requested_by,
        )
        if self.target_key and self.target_key != expected_target_key:
            raise ValueError("target_key does not match the normalized Diagnose target")
        if self.deduplication_key and self.deduplication_key != expected_deduplication_key:
            raise ValueError("deduplication_key does not match target, actor, and agent")
        if self.status in {"failed", "stale", "unavailable"} and not self.status_reason:
            raise ValueError("the Diagnose run status requires a reason")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        self.target_key = expected_target_key
        self.deduplication_key = expected_deduplication_key
        return self

    @classmethod
    def from_create_request(
        cls,
        *,
        run_id: str,
        request: DiagnoseRunCreateRequest,
        requested_by: str,
        status: DiagnoseRunStatus = "queued",
        status_reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> DiagnoseRun:
        timestamp = occurred_at or datetime.now(UTC)
        return cls(
            run_id=run_id,
            target=request.target,
            agent=request.agent,
            requested_by=requested_by,
            status=status,
            status_reason=status_reason,
            created_at=timestamp,
            updated_at=timestamp,
        )


class DiagnoseEventDraft(StrictModel):
    """An event before the durable repository assigns its sequence."""

    kind: DiagnoseEventKind
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiagnoseEvent(StrictModel):
    run_id: str = Field(min_length=1, max_length=160)
    sequence: int = Field(ge=1)
    kind: DiagnoseEventKind
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiagnoseEventReplay(StrictModel):
    """Ordered durable replay result.

    ``resync_required`` means the requested cursor predates retention.  It is
    deliberately distinct from an empty available replay so clients never
    treat lost transcript history as an idle stream.
    """

    run_id: str = Field(min_length=1, max_length=160)
    state: DiagnoseReplayState
    requested_after_sequence: int = Field(ge=0)
    next_cursor_sequence: int = Field(ge=0)
    high_water_sequence: int = Field(ge=0)
    earliest_available_sequence: int | None = Field(default=None, ge=1)
    events: tuple[DiagnoseEvent, ...] = ()

    @model_validator(mode="after")
    def validate_cursor_and_order(self) -> Self:
        if self.next_cursor_sequence < self.requested_after_sequence:
            raise ValueError("next cursor must not move backwards")
        if self.high_water_sequence < self.next_cursor_sequence:
            raise ValueError("high-water sequence must include the next cursor")

        if self.state == "resync_required":
            if self.events:
                raise ValueError("resync_required replay must not include events")
            if self.earliest_available_sequence is None:
                raise ValueError("resync_required replay requires an earliest available sequence")
            if self.earliest_available_sequence <= self.requested_after_sequence:
                raise ValueError("resync earliest sequence must be after the requested cursor")
            if self.next_cursor_sequence != self.requested_after_sequence:
                raise ValueError("resync_required replay must preserve the requested cursor")
            return self

        if self.earliest_available_sequence is not None:
            raise ValueError("available replay must not include a resync boundary")
        sequences = [event.sequence for event in self.events]
        if any(event.run_id != self.run_id for event in self.events):
            raise ValueError("replay events must belong to the requested run")
        if any(sequence <= self.requested_after_sequence for sequence in sequences):
            raise ValueError("replay events must be after the requested cursor")
        if sequences != sorted(set(sequences)):
            raise ValueError("replay events must be strictly ordered")
        expected_cursor = sequences[-1] if sequences else self.requested_after_sequence
        if self.next_cursor_sequence != expected_cursor:
            raise ValueError("next cursor must equal the last replayed event")
        return self


class DiagnoseRunCreation(StrictModel):
    """Result of an atomic create-or-get plus its initial durable event."""

    run: DiagnoseRun
    created: bool
    initial_event: DiagnoseEvent | None = None

    @model_validator(mode="after")
    def validate_initial_event(self) -> Self:
        if self.created and self.initial_event is None:
            raise ValueError("a newly created Diagnose run requires an initial event")
        if not self.created and self.initial_event is not None:
            raise ValueError("a deduplicated Diagnose run must not create another initial event")
        if self.initial_event is not None and self.initial_event.run_id != self.run.run_id:
            raise ValueError("initial event must belong to the Diagnose run")
        return self


class DiagnoseRunTransition(StrictModel):
    """Result of an atomic lifecycle transition plus its durable event."""

    run: DiagnoseRun
    changed: bool
    event: DiagnoseEvent | None = None

    @model_validator(mode="after")
    def validate_transition_event(self) -> Self:
        if self.changed and self.event is None:
            raise ValueError("a changed Diagnose run requires a durable event")
        if not self.changed and self.event is not None:
            raise ValueError("an unchanged Diagnose run must not emit an event")
        if self.event is not None and self.event.run_id != self.run.run_id:
            raise ValueError("transition event must belong to the Diagnose run")
        return self


class DiagnoseRunLaunchResult(StrictModel):
    """Create endpoint response after dispatch acceptance, not a diagnosis result."""

    run: DiagnoseRun
    created: bool
    deduplicated: bool

    @model_validator(mode="after")
    def validate_creation_flags(self) -> Self:
        if self.created == self.deduplicated:
            raise ValueError("exactly one of created or deduplicated must be true")
        return self


class DiagnoseRunList(StrictModel):
    runs: tuple[DiagnoseRun, ...] = ()
    complete: bool
    history_status: DiagnoseHistoryStatus = "available"
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_history_status(self) -> Self:
        if (not self.complete or self.history_status == "degraded") and not self.reason_codes:
            raise ValueError("partial or degraded Diagnose history requires reasons")
        return self


class DiagnoseTurnRequest(StrictModel):
    question: str = Field(min_length=1, max_length=16_000)

    @model_validator(mode="after")
    def normalize_question(self) -> Self:
        normalized = self.question.strip()
        if not normalized:
            raise ValueError("Diagnose question must not be blank")
        self.question = normalized
        return self


class DiagnoseHistoryClearResult(StrictModel):
    deleted_runs: int = Field(ge=0)


class DiagnoseCapabilities(StrictModel):
    enabled: bool
    agent: DiagnoseAgentSelection
    label: str = Field(min_length=1, max_length=120)
    disclosure_revision: str = Field(min_length=1, max_length=120)
    consented: bool
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_enabled_reason(self) -> Self:
        if not self.enabled and not self.reason_codes:
            raise ValueError("disabled Diagnose capability requires reasons")
        if self.enabled and self.reason_codes:
            raise ValueError("enabled Diagnose capability cannot carry unavailable reasons")
        return self


class DiagnoseActionExecutionRequest(StrictModel):
    """A confirmed generic command bound to one Diagnose proposal and target."""

    run_id: str = Field(min_length=1, max_length=160)
    proposal_id: str = Field(min_length=1, max_length=160)
    target: DiagnoseTarget
    command: CommandRequest

    @model_validator(mode="after")
    def validate_command_target(self) -> Self:
        if self.command.scope != self.target.scope:
            raise ValueError("command scope must match the Diagnose target")
        if self.command.resource != self.target.resource:
            raise ValueError("command resource must match the Diagnose target")
        return self


class DiagnoseActionExecutionResponse(StrictModel):
    run_id: str = Field(min_length=1, max_length=160)
    proposal_id: str = Field(min_length=1, max_length=160)
    receipt: CommandReceipt


def make_diagnose_target_key(target: DiagnoseTarget) -> str:
    """Make a stable target key without treating freshness as resource identity."""

    identity = {
        "version": _TARGET_KEY_VERSION,
        "scope": {
            "workspace_id": target.scope.workspace_id,
            "cluster_id": target.scope.cluster_id,
            "namespaces": target.scope.namespaces,
        },
        "resource": {
            "api_group": target.resource.api_group,
            "version": target.resource.version,
            "kind": target.resource.kind,
            "namespace": target.resource.namespace,
            "name": target.resource.name,
            "uid": target.resource.uid,
        },
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def make_diagnose_deduplication_key(
    *,
    target_key: str,
    agent: DiagnoseAgentSelection,
    requested_by: str,
) -> str:
    """Bind active-run deduplication to the target, actor, and agent settings."""

    identity = {
        "version": _TARGET_KEY_VERSION,
        "target_key": target_key,
        "requested_by": requested_by,
        "agent": agent.model_dump(mode="json", exclude_none=True),
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
