"""Bounded contracts for authenticated bootstrap diagnostics and release checks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from packages.contracts.modeling import StrictModel

Availability = Literal["available", "partial", "unavailable"]
Completeness = Literal["complete", "partial"]

AGENT_DIAGNOSTICS_LIMIT = 50
CAPABILITY_LIMIT = 64
EVENT_STATUS_LIMIT = 32
CONSUMER_LAG_LIMIT = 50
REASON_CODE_LIMIT = 16


class RuntimeProcessSnapshot(StrictModel):
    python_version: str = Field(min_length=1, max_length=64)
    python_implementation: str = Field(min_length=1, max_length=64)
    process_id: int = Field(ge=1)
    cpu_count: int | None = Field(default=None, ge=1)
    thread_count: int = Field(ge=1)
    uptime_seconds: float = Field(ge=0)


class EventStatusCount(StrictModel):
    status: str = Field(min_length=1, max_length=120)
    count: int = Field(ge=0)


class ConsumerLagDiagnostics(StrictModel):
    consumer: str = Field(min_length=1, max_length=253)
    subject: str = Field(min_length=1, max_length=512)
    pending: int = Field(ge=0)
    ack_pending: int = Field(ge=0)
    redelivered: int = Field(ge=0)


class EventPipelineDiagnostics(StrictModel):
    availability: Availability
    open_dead_letters: int | None = Field(default=None, ge=0)
    outbox_pending: int | None = Field(default=None, ge=0)
    processing_statuses: list[EventStatusCount] = Field(
        default_factory=list,
        max_length=EVENT_STATUS_LIMIT,
    )
    consumer_lag: list[ConsumerLagDiagnostics] = Field(
        default_factory=list,
        max_length=CONSUMER_LAG_LIMIT,
    )
    reason_codes: list[str] = Field(default_factory=list, max_length=REASON_CODE_LIMIT)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.availability == "unavailable":
            if self.open_dead_letters is not None or self.outbox_pending is not None:
                raise ValueError("unavailable event diagnostics cannot contain scalar observations")
            if self.processing_statuses or self.consumer_lag:
                raise ValueError("unavailable event diagnostics cannot contain observations")
            if not self.reason_codes:
                raise ValueError("unavailable event diagnostics require a reason")
        return self


class TimelineDiagnostics(StrictModel):
    availability: Availability
    event_count: int | None = Field(default=None, ge=0)
    oldest_occurred_at: datetime | None = None
    newest_occurred_at: datetime | None = None
    high_water_sequence: int | None = Field(default=None, ge=0)
    retained_from_sequence: int | None = Field(default=None, ge=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=REASON_CODE_LIMIT)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        observations = (
            self.event_count,
            self.oldest_occurred_at,
            self.newest_occurred_at,
            self.high_water_sequence,
            self.retained_from_sequence,
        )
        if self.availability == "unavailable":
            if any(value is not None for value in observations):
                raise ValueError("unavailable timeline diagnostics cannot contain observations")
            if not self.reason_codes:
                raise ValueError("unavailable timeline diagnostics require a reason")
        return self


class AgentInventorySnapshotDiagnostics(StrictModel):
    status: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=120)
    collected_at: datetime
    resource_count: int = Field(ge=0)


class AgentDiagnosticsItem(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    environment: str = Field(max_length=120)
    registration_status: str = Field(min_length=1, max_length=120)
    connection_status: Literal["online", "stale", "never_connected"]
    agent_id: str | None = Field(default=None, min_length=1, max_length=253)
    agent_status: str | None = Field(default=None, min_length=1, max_length=120)
    last_seen_at: datetime | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=CAPABILITY_LIMIT)
    latest_inventory: AgentInventorySnapshotDiagnostics | None = None


class AgentCollectionDiagnostics(StrictModel):
    availability: Availability
    items: list[AgentDiagnosticsItem] = Field(
        default_factory=list,
        max_length=AGENT_DIAGNOSTICS_LIMIT,
    )
    reason_codes: list[str] = Field(default_factory=list, max_length=REASON_CODE_LIMIT)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.availability == "unavailable":
            if self.items:
                raise ValueError("unavailable agent diagnostics cannot contain items")
            if not self.reason_codes:
                raise ValueError("unavailable agent diagnostics require a reason")
        return self


class RuntimeDiagnosticsResponse(StrictModel):
    observed_at: datetime
    completeness: Completeness
    runtime: RuntimeProcessSnapshot
    event_pipeline: EventPipelineDiagnostics
    timeline: TimelineDiagnostics
    agent_collection: AgentCollectionDiagnostics
    reason_codes: list[str] = Field(default_factory=list, max_length=REASON_CODE_LIMIT)

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        sections = (self.event_pipeline, self.timeline, self.agent_collection)
        degraded = any(section.availability != "available" for section in sections)
        if self.completeness == "complete" and (degraded or self.reason_codes):
            raise ValueError("complete runtime diagnostics cannot contain degraded sections")
        if self.completeness == "partial" and not self.reason_codes:
            raise ValueError("partial runtime diagnostics require a reason")
        return self


class VersionCheckResponse(StrictModel):
    availability: Availability
    current_version: str = Field(min_length=1, max_length=64)
    latest_version: str | None = Field(default=None, min_length=1, max_length=64)
    update_available: bool | None = None
    release_url: str | None = Field(default=None, max_length=2048)
    release_notes: str | None = Field(default=None, max_length=2000)
    observed_at: datetime
    reason_codes: list[str] = Field(default_factory=list, max_length=REASON_CODE_LIMIT)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        remote_values = (
            self.latest_version,
            self.update_available,
            self.release_url,
            self.release_notes,
        )
        if self.availability == "unavailable":
            if any(value is not None for value in remote_values):
                raise ValueError("unavailable version check cannot contain remote observations")
            if not self.reason_codes:
                raise ValueError("unavailable version check requires a reason")
        elif self.latest_version is None or self.update_available is None:
            raise ValueError("available version check requires latest version and update decision")
        return self
