"""Agent-observed traffic source controls and exact NetworkPolicy verdicts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from packages.config.constants import Command
from packages.contracts.modeling import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef
from packages.contracts.traffic.observations import TrafficScopeCoverage

TRAFFIC_SOURCE_OBSERVER_CAPABILITY = Command.TRAFFIC_SOURCE_OBSERVER_CAPABILITY
TRAFFIC_SOURCE_SELECT_CAPABILITY = Command.TRAFFIC_SOURCE_SELECT_CAPABILITY
TRAFFIC_SOURCE_CONNECT_CAPABILITY = Command.TRAFFIC_SOURCE_CONNECT_CAPABILITY
TRAFFIC_SOURCE_SELECT_ACTION = Command.TRAFFIC_SOURCE_SELECT_ACTION
TRAFFIC_SOURCE_CONNECT_ACTION = Command.TRAFFIC_SOURCE_CONNECT_ACTION

TrafficSourceStatus = Literal["available", "not_detected", "error"]
TrafficSourceActionKind = Literal["select", "connect"]
TrafficVerdict = Literal["allowed", "denied", "indeterminate"]


class TrafficSourceActionDescriptor(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    kind: TrafficSourceActionKind
    label: str = Field(min_length=1, max_length=160)
    enabled: bool
    confirmation_required: bool = True
    reason_code: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def unavailable_action_requires_reason(self) -> Self:
        if self.enabled == (self.reason_code is not None):
            raise ValueError("traffic source action availability and reason are inconsistent")
        return self


class TrafficSourceDescriptor(StrictModel):
    key: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$", max_length=64)
    label: str = Field(min_length=1, max_length=120)
    status: TrafficSourceStatus
    version: str | None = Field(default=None, max_length=120)
    native: bool = False
    message: str = Field(min_length=1, max_length=500)
    actions: tuple[TrafficSourceActionDescriptor, ...] = ()

    @field_validator("actions")
    @classmethod
    def actions_are_unique(
        cls,
        actions: tuple[TrafficSourceActionDescriptor, ...],
    ) -> tuple[TrafficSourceActionDescriptor, ...]:
        identities = [item.id for item in actions]
        if len(identities) != len(set(identities)):
            raise ValueError("traffic source actions must be unique")
        return actions


class TrafficDetectedCluster(StrictModel):
    platform: str = Field(min_length=1, max_length=80)
    cni: str = Field(min_length=1, max_length=80)
    dataplane_v2: bool = False
    kubernetes_version: str | None = Field(default=None, max_length=120)


class TrafficClusterSourceCatalog(StrictModel):
    scope: ClusterScope
    freshness: Literal["live", "stale", "partial", "disconnected"]
    observed_at: str | None = None
    active_source: str | None = Field(default=None, max_length=64)
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    cluster: TrafficDetectedCluster | None = None
    sources: tuple[TrafficSourceDescriptor, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unavailable_catalog_has_reason(self) -> Self:
        if not self.sources and not self.reason_codes:
            raise ValueError("empty traffic source catalog requires a reason")
        keys = [source.key for source in self.sources]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("traffic source catalog must be unique and sorted")
        if self.active_source and self.active_source not in set(keys):
            raise ValueError("active traffic source must exist in the observed catalog")
        return self


class TrafficSourcesResponse(StrictModel):
    availability: Literal["available", "partial", "unavailable"]
    coverage: TrafficScopeCoverage
    clusters: tuple[TrafficClusterSourceCatalog, ...]
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def availability_has_reason(self) -> Self:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("incomplete source discovery requires a reason")
        cluster_ids = [item.scope.cluster_id for item in self.clusters]
        if cluster_ids != sorted(cluster_ids) or len(cluster_ids) != len(set(cluster_ids)):
            raise ValueError("traffic cluster source catalogs must be unique and sorted")
        return self


class TrafficSourceCommandRequest(StrictModel):
    scope: ClusterScope
    source_key: str = Field(
        pattern=r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$",
        max_length=64,
    )
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal[True]
    reason: str = Field(min_length=3, max_length=500)


class TrafficSourceAgentCommandPayload(StrictModel):
    source_key: str = Field(
        pattern=r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$",
        max_length=64,
    )
    capability_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_invalidations: tuple[
        Literal["traffic.sources", "traffic.flows", "traffic.overview"],
        ...,
    ]

    @field_validator("cache_invalidations")
    @classmethod
    def invalidations_are_exact_and_unique(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        expected = ("traffic.sources", "traffic.flows", "traffic.overview")
        if values != expected:
            raise ValueError("traffic cache invalidations must use the canonical order")
        return values


class NetworkPolicyEvaluationCoverage(StrictModel):
    state: Literal["complete", "partial"]
    evaluated_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partial_coverage_has_reason(self) -> Self:
        if self.state == "partial" and not self.reason_codes:
            raise ValueError("partial NetworkPolicy coverage requires a reason")
        return self


class SelectingNetworkPolicy(StrictModel):
    resource: ResourceRef
    effect: Literal["allow", "deny", "unknown"]
    reason: str = Field(min_length=1, max_length=500)


class NetworkPolicyEvaluationResponse(StrictModel):
    evaluated_pod: ResourceRef
    peer_pod: ResourceRef
    direction: Literal["ingress", "egress"]
    port: int = Field(ge=1, le=65_535)
    protocol: Literal["TCP", "UDP", "SCTP"]
    selecting_policies: tuple[SelectingNetworkPolicy, ...]
    verdict: TrafficVerdict
    coverage: NetworkPolicyEvaluationCoverage
