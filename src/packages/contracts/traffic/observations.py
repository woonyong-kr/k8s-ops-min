"""Read-only, evidence-first traffic observation contracts.

The contract deliberately distinguishes an observed empty flow set from an
unavailable collector.  A consumer must never render an empty graph or zero
traffic count when the product has not collected traffic evidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ClusterScope

TrafficAvailability = Literal["available", "partial", "unavailable"]
TrafficSince = Literal["1m", "5m", "15m", "1h"]
TrafficSort = Literal["connections", "last_seen", "source", "destination"]
TrafficSortOrder = Literal["asc", "desc"]
TrafficProtocol = Literal["tcp", "udp", "http", "grpc", "dns", "unknown"]
TrafficVerdict = Literal["forwarded", "dropped", "error", "unknown"]

TRAFFIC_SINCE_SECONDS: dict[TrafficSince, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3_600,
}

MAX_TRAFFIC_PAGE_SIZE = 200
MAX_TRAFFIC_TOTAL_COUNT = 9_007_199_254_740_991

TRAFFIC_CARETTA_FLOW_METRIC = "traffic_caretta_flows"
TRAFFIC_HUBBLE_FLOW_METRIC = "traffic_hubble_flows"
TRAFFIC_ISTIO_FLOW_METRIC = "traffic_istio_flows"


class TrafficScopeCoverage(StrictModel):
    """Authorized inventory scope used to evaluate traffic availability."""

    availability: TrafficAvailability
    scopes: tuple[ClusterScope, ...] = ()
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_scope_has_a_reason(self) -> TrafficScopeCoverage:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete traffic scope coverage requires a reason")
        return self


class TrafficObservationStatus(StrictModel):
    """State of the traffic evidence collector for the selected scope."""

    availability: Literal["unavailable"] = "unavailable"
    observed_at: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


class TrafficObservedObservationStatus(StrictModel):
    """Exact provider observations materialized from outbound Agent evidence."""

    availability: Literal["available", "partial"]
    observed_at: str = Field(min_length=1)
    since: TrafficSince
    source_keys: tuple[str, ...] = Field(min_length=1, max_length=16)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partial_observation_has_a_reason(self) -> TrafficObservedObservationStatus:
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial traffic observation requires a reason")
        if len(self.source_keys) != len(set(self.source_keys)):
            raise ValueError("traffic source keys must be unique")
        return self


class TrafficObservationSummary(StrictModel):
    """Counts are null until a collector materializes a traffic observation."""

    availability: Literal["unavailable"] = "unavailable"
    total_flow_count: None = None
    denied_flow_count: None = None
    external_flow_count: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


class TrafficObservedObservationSummary(StrictModel):
    availability: Literal["available", "partial"]
    total_flow_count: int = Field(ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)
    denied_flow_count: int = Field(ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)
    external_flow_count: int = Field(ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def observed_summary_is_consistent(self) -> TrafficObservedObservationSummary:
        if self.denied_flow_count > self.total_flow_count:
            raise ValueError("denied traffic count cannot exceed total flow count")
        if self.external_flow_count > self.total_flow_count:
            raise ValueError("external traffic count cannot exceed total flow count")
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial traffic summary requires a reason")
        return self


class TrafficEndpoint(StrictModel):
    """Provider-observed endpoint without manufacturing a Kubernetes UID."""

    cluster_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=253)
    kind: str = Field(min_length=1, max_length=120)
    workload: str | None = Field(default=None, max_length=253)
    service: str | None = Field(default=None, max_length=253)
    ip: str | None = Field(default=None, max_length=255)
    identity_stability: Literal["provider_observed"] = "provider_observed"


class TrafficRelationship(StrictModel):
    """One bounded, provider-observed flow edge with cluster identity."""

    flow_id: str = Field(min_length=1, max_length=128)
    source_key: str = Field(min_length=1, max_length=80)
    source: TrafficEndpoint
    target: TrafficEndpoint
    protocol: TrafficProtocol
    port: int | None = Field(default=None, ge=1, le=65_535)
    verdict: TrafficVerdict
    connections: int = Field(ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)
    bytes_sent: int | None = Field(default=None, ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)
    bytes_received: int | None = Field(default=None, ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)
    observed_at: str = Field(min_length=1)


class TrafficProtocolFacet(StrictModel):
    value: TrafficProtocol
    count: int = Field(ge=1, le=MAX_TRAFFIC_TOTAL_COUNT)


class TrafficVerdictFacet(StrictModel):
    value: TrafficVerdict
    count: int = Field(ge=1, le=MAX_TRAFFIC_TOTAL_COUNT)


class TrafficFlowFacets(StrictModel):
    protocols: tuple[TrafficProtocolFacet, ...] = Field(max_length=6)
    verdicts: tuple[TrafficVerdictFacet, ...] = Field(max_length=4)


class TrafficRelationships(StrictModel):
    """Never use an empty edge list to represent a missing collector."""

    availability: Literal["unavailable"] = "unavailable"
    edges: None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)


class TrafficObservedRelationships(StrictModel):
    availability: Literal["available", "partial"]
    edges: tuple[TrafficRelationship, ...] = Field(max_length=MAX_TRAFFIC_PAGE_SIZE)
    total_count: int = Field(ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=8192)
    facets: TrafficFlowFacets
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def page_is_consistent(self) -> TrafficObservedRelationships:
        if len(self.edges) > self.total_count:
            raise ValueError("traffic page cannot exceed its filtered total")
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("traffic pagination requires a cursor exactly when more rows exist")
        if self.availability == "partial" and not self.reason_codes:
            raise ValueError("partial traffic relationships require a reason")
        return self


class TrafficOverviewResponse(StrictModel):
    scope_coverage: TrafficScopeCoverage
    observation: TrafficObservedObservationStatus | TrafficObservationStatus
    summary: TrafficObservedObservationSummary | TrafficObservationSummary
    relationships: TrafficObservedRelationships | TrafficRelationships
    refresh_after_seconds: int = Field(ge=1, le=3_600)
