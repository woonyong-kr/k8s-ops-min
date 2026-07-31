"""Server-owned workload rightsizing observations and recommendations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef

MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_RIGHTSIZING_WORKLOADS = 200
MAX_RIGHTSIZING_ROWS = 400
MAX_RIGHTSIZING_FAILURES = 200

RightsizingAvailability = Literal["available", "partial", "unavailable"]
RightsizingResource = Literal["cpu", "memory"]
RightsizingUnit = Literal["millicores", "bytes"]
RightsizingFit = Literal[
    "balanced",
    "oversized",
    "under_requested",
    "missing_request",
    "insufficient_history",
]
RightsizingAction = Literal["increase", "reduction", "review", "in_range", "need_data"]
RightsizingConfidence = Literal["high", "medium", "low", "none"]
RightsizingSignal = Literal[
    "hpa",
    "oom",
    "bursty",
    "throttling",
    "query_error",
    "history_incomplete",
]


class RightsizingQuantity(StrictModel):
    unit: RightsizingUnit
    value: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)


class RightsizingProvenance(StrictModel):
    collector: str = Field(min_length=1, max_length=120)
    algorithm_revision: str = Field(min_length=1, max_length=120)
    source_revision: str = Field(min_length=1, max_length=160)
    window_started_at: str = Field(min_length=1)
    window_ended_at: str = Field(min_length=1)
    sample_interval_seconds: int = Field(ge=1, le=86_400)


class RightsizingMetricRecommendation(StrictModel):
    container: str = Field(min_length=1, max_length=253)
    resource: RightsizingResource
    fit: RightsizingFit
    action: RightsizingAction
    confidence: RightsizingConfidence
    current_request: RightsizingQuantity | None = None
    observed_demand: RightsizingQuantity | None = None
    recommended_request: RightsizingQuantity | None = None
    sample_count: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    expected_samples: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    coverage_basis_points: int = Field(ge=0, le=10_000)
    signals: tuple[RightsizingSignal, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def recommendation_is_consistent(self) -> RightsizingMetricRecommendation:
        expected_unit = "millicores" if self.resource == "cpu" else "bytes"
        quantities = (
            self.current_request,
            self.observed_demand,
            self.recommended_request,
        )
        if any(quantity is not None and quantity.unit != expected_unit for quantity in quantities):
            raise ValueError("rightsizing quantity unit must match its resource")
        if self.action in ("increase", "reduction") and self.recommended_request is None:
            raise ValueError("actionable rightsizing result requires a recommendation")
        if self.sample_count > self.expected_samples:
            raise ValueError("sample count cannot exceed expected samples")
        if len(self.signals) != len(set(self.signals)):
            raise ValueError("rightsizing signals must be unique")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("rightsizing reasons must be unique")
        return self


class RightsizingImpact(StrictModel):
    replicas: int = Field(ge=0, le=100_000)
    cpu_millicores_change: int = Field(
        ge=-MAX_SAFE_JSON_INTEGER,
        le=MAX_SAFE_JSON_INTEGER,
    )
    memory_bytes_change: int = Field(
        ge=-MAX_SAFE_JSON_INTEGER,
        le=MAX_SAFE_JSON_INTEGER,
    )


class RightsizingObservedWorkload(StrictModel):
    availability: Literal["available", "partial"]
    resource: ResourceRef
    observed_at: str = Field(min_length=1)
    freshness: Literal["live", "stale", "partial", "disconnected"]
    provenance: RightsizingProvenance
    replicas: int = Field(ge=0, le=100_000)
    scaled_to_zero: bool
    classification: RightsizingAction
    impact: RightsizingImpact
    rows: tuple[RightsizingMetricRecommendation, ...] = Field(
        min_length=1,
        max_length=MAX_RIGHTSIZING_ROWS,
    )
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def workload_evidence_is_consistent(self) -> RightsizingObservedWorkload:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial rightsizing evidence requires reasons")
        identities = tuple((row.container, row.resource) for row in self.rows)
        if len(identities) != len(set(identities)):
            raise ValueError("rightsizing container resource rows must be unique")
        if self.impact.replicas != self.replicas:
            raise ValueError("rightsizing impact replicas must match workload replicas")
        if self.scaled_to_zero and (
            self.impact.cpu_millicores_change != 0 or self.impact.memory_bytes_change != 0
        ):
            raise ValueError("scaled-to-zero workload impact must remain zero")
        return self


class RightsizingUnavailableWorkload(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    reason_codes: tuple[str, ...] = Field(min_length=1)


RightsizingWorkloadEvidence = RightsizingObservedWorkload | RightsizingUnavailableWorkload


class RightsizingWorkloadFailure(StrictModel):
    resource: ResourceRef | None = None
    reason_code: str = Field(min_length=1, max_length=160)


class RightsizingScanCoverage(StrictModel):
    workloads_discovered: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    workloads_evaluated: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    workloads_with_data: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    truncated: bool

    @model_validator(mode="after")
    def counts_are_monotonic(self) -> RightsizingScanCoverage:
        if not (self.workloads_with_data <= self.workloads_evaluated <= self.workloads_discovered):
            raise ValueError("rightsizing coverage counts must be monotonic")
        return self


class RightsizingObservedScan(StrictModel):
    availability: Literal["available", "partial"]
    observed_at: str = Field(min_length=1)
    provenance: RightsizingProvenance
    coverage: RightsizingScanCoverage
    workloads: tuple[RightsizingObservedWorkload, ...] = Field(max_length=MAX_RIGHTSIZING_WORKLOADS)
    failures: tuple[RightsizingWorkloadFailure, ...] = Field(max_length=MAX_RIGHTSIZING_FAILURES)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def scan_is_consistent(self) -> RightsizingObservedScan:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial rightsizing scan requires reasons")
        if self.availability == "available" and self.failures:
            raise ValueError("available rightsizing scan cannot contain failures")
        uids = tuple(workload.resource.uid for workload in self.workloads)
        if len(uids) != len(set(uids)):
            raise ValueError("rightsizing scan workloads must be unique")
        return self


class RightsizingUnavailableScan(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    reason_codes: tuple[str, ...] = Field(min_length=1)


class RightsizingScanResponse(StrictModel):
    scope: ClusterScope
    namespace_scope: tuple[str, ...]
    result: RightsizingObservedScan | RightsizingUnavailableScan
    refresh_after_seconds: int = Field(ge=1, le=3600)
