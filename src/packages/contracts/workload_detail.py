"""Typed, read-only workload-detail observation boundary.

The inventory store retains raw Kubernetes payloads for server-side ingestion
and correlation.  This module is deliberately *not* a mirror of those
payloads: browser consumers receive only explicitly allowlisted observation
fields.  In particular, manifests, Kubernetes ``status`` objects, secret
values, annotations, and raw inventory maps are outside this contract.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import CapabilitySet, ClusterScope, ResourceRef
from packages.contracts.rightsizing import RightsizingWorkloadEvidence

WorkloadAvailability = Literal["available", "partial", "unavailable"]
WorkloadFeatureName = Literal[
    "overview",
    "pods",
    "events",
    "logs",
    "metrics",
    "topology",
    "timeline",
    "rbac",
    "gitops",
    "helm",
    "operations",
    "yaml",
    "compare",
    "execution",
    "rightsizing",
]
WorkloadLogStreamKind = Literal["deployments", "statefulsets", "daemonsets"]


class WorkloadObservationCoverage(StrictModel):
    """Trust statement for the inventory cut used by this detail response."""

    availability: WorkloadAvailability
    observation_snapshot_id: str = Field(min_length=1)
    latest_snapshot_id: str = Field(min_length=1)
    observed_at: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def incomplete_coverage_has_reasons(self) -> WorkloadObservationCoverage:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial or unavailable workload coverage requires reasons")
        return self


class WorkloadFeatureAvailability(StrictModel):
    """A feature is surfaced only when its source contract exists."""

    name: WorkloadFeatureName
    availability: WorkloadAvailability
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unavailable_feature_has_reasons(self) -> WorkloadFeatureAvailability:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("unavailable workload feature requires reasons")
        return self


class WorkloadLogStreamCapability(StrictModel):
    """Exact existing SSE capability; no client-side kind lookup is required."""

    availability: WorkloadAvailability
    stream_kind: WorkloadLogStreamKind | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def stream_capability_is_consistent(self) -> WorkloadLogStreamCapability:
        if self.availability == "available" and self.stream_kind is None:
            raise ValueError("available workload log stream requires a stream kind")
        if self.availability != "available" and self.reason_codes == ():
            raise ValueError("unavailable workload log stream requires reasons")
        if self.availability != "available" and self.stream_kind is not None:
            raise ValueError("unavailable workload log stream cannot expose a stream kind")
        return self


class WorkloadReplicaObservation(StrictModel):
    desired: int | None = Field(default=None, ge=0)
    ready: int | None = Field(default=None, ge=0)
    available: int | None = Field(default=None, ge=0)
    updated: int | None = Field(default=None, ge=0)
    unavailable: int | None = Field(default=None, ge=0)


class WorkloadLabel(StrictModel):
    key: str = Field(min_length=1)
    value: str


class WorkloadObservation(StrictModel):
    resource: ResourceRef
    health: str = Field(min_length=1)
    replicas: WorkloadReplicaObservation
    labels: tuple[WorkloadLabel, ...] = ()
    observed_at: str | None = None


class WorkloadPodObservation(StrictModel):
    resource: ResourceRef
    health: str = Field(min_length=1)
    observed_at: str | None = None


class WorkloadPodCollection(StrictModel):
    availability: WorkloadAvailability
    items: tuple[WorkloadPodObservation, ...] = ()
    excluded_count: int = Field(default=0, ge=0)
    reason_codes: tuple[str, ...] = ()


class WorkloadEventObservation(StrictModel):
    resource: ResourceRef
    event_type: str | None = None
    reason: str | None = None
    occurrence_count: int | None = Field(default=None, ge=0)
    last_occurred_at: str | None = None


class WorkloadEventCollection(StrictModel):
    availability: WorkloadAvailability
    items: tuple[WorkloadEventObservation, ...] = ()
    excluded_count: int = Field(default=0, ge=0)
    reason_codes: tuple[str, ...] = ()


class WorkloadDetail(StrictModel):
    scope: ClusterScope
    observation: WorkloadObservation
    coverage: WorkloadObservationCoverage
    pods: WorkloadPodCollection
    events: WorkloadEventCollection
    log_stream: WorkloadLogStreamCapability
    rightsizing: RightsizingWorkloadEvidence
    capabilities: CapabilitySet
    features: tuple[WorkloadFeatureAvailability, ...]

    @model_validator(mode="after")
    def feature_and_capability_identity_is_consistent(self) -> WorkloadDetail:
        names = tuple(feature.name for feature in self.features)
        if len(names) != len(set(names)):
            raise ValueError("workload feature names must be unique")
        if self.capabilities.scope != self.scope:
            raise ValueError("workload capability scope must match detail scope")
        if self.capabilities.resource != self.observation.resource:
            raise ValueError("workload capability resource must match observation resource")
        return self


class WorkloadDetailResponse(StrictModel):
    detail: WorkloadDetail
