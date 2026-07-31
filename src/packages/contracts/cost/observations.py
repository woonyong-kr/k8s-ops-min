"""Evidence-first Cost contracts.

Amounts, currencies, and recommendations remain null until an authorized
collector persists provider billing or allocation observations.  Consumers
must distinguish that condition from an observed zero cost.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef

CostAvailability = Literal["available", "partial", "unavailable"]
CostTimeRange = Literal["6h", "24h", "7d"]
CostWorkloadKind = Literal["Deployment", "StatefulSet", "DaemonSet"]

COST_NAMESPACE_HOURLY_METRIC = "opencost_namespace_hourly_rate"
COST_NAMESPACE_STORAGE_METRIC = "opencost_namespace_storage_rate"
COST_POD_CPU_HOURLY_METRIC = "opencost_pod_cpu_hourly_rate"
COST_POD_MEMORY_HOURLY_METRIC = "opencost_pod_memory_hourly_rate"
COST_POD_CPU_USE_METRIC = "opencost_pod_cpu_allocation_use"
COST_POD_MEMORY_USE_METRIC = "opencost_pod_memory_allocation_use"
COST_EVIDENCE_METRICS = (
    COST_NAMESPACE_HOURLY_METRIC,
    COST_NAMESPACE_STORAGE_METRIC,
    COST_POD_CPU_HOURLY_METRIC,
    COST_POD_MEMORY_HOURLY_METRIC,
    COST_POD_CPU_USE_METRIC,
    COST_POD_MEMORY_USE_METRIC,
)

MAX_COST_TREND_SERIES = 8
MAX_COST_TREND_POINTS = 480
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_COST_NODE_PAGE_SIZE = 200


class CostScopeCoverage(StrictModel):
    availability: CostAvailability
    scopes: tuple[ClusterScope, ...] = ()
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_scope_has_a_reason(self) -> CostScopeCoverage:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete cost scope coverage requires a reason")
        return self


class CostObservationStatus(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    observed_at: None = None
    currency: None = None
    data_window: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


class CostObservedObservationStatus(StrictModel):
    availability: Literal["available", "partial"]
    observed_at: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    data_window: str = Field(min_length=1, max_length=32)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partial_observation_has_a_reason(self) -> CostObservedObservationStatus:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial cost observation requires a reason")
        return self


class CostObservationSummary(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    hourly_cost: None = None
    monthly_projection: None = None
    storage_cost: None = None
    idle_cost: None = None
    efficiency: None = None
    savings_recommendations: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


class CostObservedObservationSummary(StrictModel):
    """Observed currency values use integer micro-units; efficiency uses basis points."""

    availability: Literal["available", "partial"]
    hourly_cost: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    monthly_projection: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    storage_cost: int | None = Field(default=None, ge=0, le=MAX_SAFE_JSON_INTEGER)
    idle_cost: int | None = Field(default=None, ge=0, le=MAX_SAFE_JSON_INTEGER)
    efficiency: int | None = Field(default=None, ge=0, le=10_000)
    savings_recommendations: None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partial_summary_has_a_reason(self) -> CostObservedObservationSummary:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial cost summary requires a reason")
        return self


class CostTrendPoint(StrictModel):
    timestamp: int = Field(ge=0)
    rate_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)


class CostTrendSeries(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=240)
    points: tuple[CostTrendPoint, ...] = Field(
        min_length=2,
        max_length=MAX_COST_TREND_POINTS,
    )

    @model_validator(mode="after")
    def points_are_strictly_ordered(self) -> CostTrendSeries:
        timestamps = tuple(point.timestamp for point in self.points)
        if timestamps != tuple(sorted(set(timestamps))):
            raise ValueError("cost trend points must have unique ascending timestamps")
        return self


class CostObservedTrend(StrictModel):
    availability: Literal["available", "partial"]
    range: CostTimeRange
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    series: tuple[CostTrendSeries, ...] = Field(
        min_length=1,
        max_length=MAX_COST_TREND_SERIES,
    )
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partial_trend_has_a_reason(self) -> CostObservedTrend:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial cost trend requires a reason")
        keys = tuple(series.key for series in self.series)
        if len(keys) != len(set(keys)):
            raise ValueError("cost trend series keys must be unique")
        return self


class CostUnavailableTrend(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    range: CostTimeRange
    currency: None = None
    series: tuple[()] = ()
    reason_codes: tuple[str, ...] = Field(min_length=1)


class CostCurrentAllocation(StrictModel):
    """One server-computed workload allocation snapshot in integer micro-units."""

    replicas: int = Field(ge=0, le=100_000)
    hourly_rate_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    projected_daily_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    projected_monthly_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    cpu_rate_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    memory_rate_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    cpu_allocation_use_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    memory_allocation_use_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    cpu_usage_window_seconds: int | None = Field(default=None, ge=1, le=86_400)
    memory_usage_window_seconds: int | None = Field(default=None, ge=1, le=86_400)

    @model_validator(mode="after")
    def component_rates_do_not_exceed_total(self) -> CostCurrentAllocation:
        if self.cpu_rate_micros + self.memory_rate_micros > self.hourly_rate_micros:
            raise ValueError("workload component rates cannot exceed the hourly total")
        if (self.cpu_allocation_use_basis_points is None) != (
            self.cpu_usage_window_seconds is None
        ):
            raise ValueError("CPU allocation use and its window must be available together")
        if (self.memory_allocation_use_basis_points is None) != (
            self.memory_usage_window_seconds is None
        ):
            raise ValueError("memory allocation use and its window must be available together")
        return self


class CostObservedWorkloadAllocation(StrictModel):
    availability: Literal["available", "partial"]
    observed_at: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    current: CostCurrentAllocation
    trend: CostObservedTrend | CostUnavailableTrend
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def observed_workload_is_consistent(self) -> CostObservedWorkloadAllocation:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial workload cost requires a reason")
        if isinstance(self.trend, CostObservedTrend) and self.trend.currency != self.currency:
            raise ValueError("workload current and trend currencies must match")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("workload cost reasons must be unique")
        return self


class CostUnavailableWorkloadAllocation(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reasons_are_unique(self) -> CostUnavailableWorkloadAllocation:
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("workload cost reasons must be unique")
        return self


CostWorkloadAllocation = CostObservedWorkloadAllocation | CostUnavailableWorkloadAllocation


class CostOverviewResponse(StrictModel):
    scope_coverage: CostScopeCoverage
    observation: CostObservedObservationStatus | CostObservationStatus
    summary: CostObservedObservationSummary | CostObservationSummary
    trend: CostObservedTrend | CostUnavailableTrend
    refresh_after_seconds: int = Field(ge=1, le=3600)
    trend_refresh_after_seconds: int = Field(ge=1, le=3600)
    nodes_refresh_after_seconds: int = Field(ge=1, le=3600)


class CostNodeCapacity(StrictModel):
    cpu_mcores: float | None = Field(default=None, ge=0)
    memory_mib: float | None = Field(default=None, ge=0)
    pods: int | None = Field(default=None, ge=0)


class CostNodeUsage(StrictModel):
    availability: CostAvailability
    observed_at: str | None = None
    cpu_mcores: float | None = Field(default=None, ge=0)
    memory_mib: float | None = Field(default=None, ge=0)
    cpu_utilization_percent: float | None = Field(default=None, ge=0)
    memory_utilization_percent: float | None = Field(default=None, ge=0)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def availability_matches_evidence(self) -> CostNodeUsage:
        values = (
            self.cpu_mcores,
            self.memory_mib,
            self.cpu_utilization_percent,
            self.memory_utilization_percent,
        )
        observed = sum(value is not None for value in values)
        if self.availability == "available" and (
            self.observed_at is None or observed != len(values)
        ):
            raise ValueError("available node usage requires a complete observed measurement")
        if self.availability == "partial" and (self.observed_at is None or observed == 0):
            raise ValueError("partial node usage requires observed measurement evidence")
        if self.availability == "unavailable" and (self.observed_at is not None or observed):
            raise ValueError("unavailable node usage cannot carry measurements")
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete node usage requires a reason")
        if self.availability == "available" and self.reason_codes:
            raise ValueError("available node usage cannot carry incomplete reasons")
        return self


class CostNodePricing(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    currency: None = None
    hourly_rate_micros: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


class CostNodePricingCoverage(StrictModel):
    availability: Literal["unavailable"] = "unavailable"
    reason_codes: tuple[str, ...] = Field(min_length=1)


class CostNodeItem(StrictModel):
    resource: ResourceRef
    cluster_id: str = Field(min_length=1)
    cluster_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    provider_id: str | None = None
    instance_type: str | None = None
    zone: str | None = None
    capacity_type: str | None = None
    status: str = Field(min_length=1)
    observed_at: str = Field(min_length=1)
    capacity: CostNodeCapacity
    usage: CostNodeUsage
    pricing: CostNodePricing


class CostNodePageResponse(StrictModel):
    scope_coverage: CostScopeCoverage
    items: tuple[CostNodeItem, ...] = Field(max_length=MAX_COST_NODE_PAGE_SIZE)
    total: int = Field(ge=0)
    count_completeness: Literal["exact", "partial", "unavailable"]
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=8192)
    snapshot_revision: int = Field(ge=0)
    pricing_coverage: CostNodePricingCoverage
    refresh_after_seconds: int = Field(ge=1, le=3600)

    @model_validator(mode="after")
    def pagination_is_consistent(self) -> CostNodePageResponse:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("node cost pagination requires a cursor exactly when more rows exist")
        if self.total < len(self.items):
            raise ValueError("node cost total cannot be smaller than the current page")
        return self
