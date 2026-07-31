"""Strict, safe comparison documents for the browser Compare surface.

This is intentionally not a Kubernetes-object or YAML transport contract.  A
document contains only typed configuration facts that a registered projection
knows how to derive safely from inventory.  Raw objects, annotations, labels,
observed runtime state, and secret material are not representable here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import ClusterScope, ResourceRef

CompareAvailability = Literal["available", "partial", "unavailable"]
CompareProjectionKind = Literal["workload_replicas", "service_ports"]
ComparePresentationMode = Literal["side-by-side", "unified"]
ServiceProtocol = Literal["TCP", "UDP", "SCTP"]
ServiceType = Literal["ClusterIP", "NodePort", "LoadBalancer", "ExternalName"]


class CompareDescriptor(StrictModel):
    """Server-owned mapping from the source route key to an exact safe projection."""

    route_kind: str = Field(min_length=1, max_length=120)
    api_group: str = Field(max_length=253)
    api_version: str = Field(min_length=1, max_length=63)
    kubernetes_kind: str = Field(min_length=1, max_length=120)
    resource_type: str = Field(min_length=1, max_length=80)
    projection_kind: CompareProjectionKind


class CompareMetadata(StrictModel):
    name: str = Field(min_length=1, max_length=253)
    namespace: str | None = Field(default=None, max_length=63)


class WorkloadReplicaProjection(StrictModel):
    projection_kind: Literal["workload_replicas"] = "workload_replicas"
    replicas: int | None = Field(default=None, ge=0)


class ServicePortProjection(StrictModel):
    name: str | None = Field(default=None, max_length=63)
    port: int = Field(ge=1, le=65535)
    protocol: ServiceProtocol | None = None
    target_port_name: str | None = Field(default=None, max_length=63)
    target_port_number: int | None = Field(default=None, ge=1, le=65535)
    node_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def target_port_is_unambiguous(self) -> ServicePortProjection:
        if self.target_port_name is not None and self.target_port_number is not None:
            raise ValueError("a service port can have only one target port representation")
        return self


class ServicePortsProjection(StrictModel):
    projection_kind: Literal["service_ports"] = "service_ports"
    service_type: ServiceType | None = None
    ports: tuple[ServicePortProjection, ...] = ()
    excluded_port_count: int = Field(default=0, ge=0)


ComparableProjection = WorkloadReplicaProjection | ServicePortsProjection


class CompareProvenance(StrictModel):
    """Observation trust facts for one side, without leaking inventory internals."""

    source_kind: Literal["inventory_snapshot"] = "inventory_snapshot"
    observation_snapshot_id: str = Field(min_length=1)
    latest_snapshot_id: str = Field(min_length=1)
    observed_at: str | None = None
    availability: CompareAvailability
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partial_provenance_has_reasons(self) -> CompareProvenance:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial comparison provenance requires reasons")
        return self


class ComparableManifest(StrictModel):
    """One allowlisted comparison side; never a raw Kubernetes manifest."""

    projection_version: Literal["safe-manifest-v1"] = "safe-manifest-v1"
    resource: ResourceRef
    metadata: CompareMetadata
    projection: ComparableProjection
    provenance: CompareProvenance
    omitted_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def metadata_matches_exact_resource(self) -> ComparableManifest:
        if (
            self.metadata.name != self.resource.name
            or self.metadata.namespace != self.resource.namespace
        ):
            raise ValueError("comparison metadata must match the exact resource identity")
        return self


class CompareCoverage(StrictModel):
    availability: CompareAvailability
    latest_snapshot_id: str = Field(min_length=1)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def partial_coverage_has_reasons(self) -> CompareCoverage:
        if self.availability != "available" and not self.reason_codes:
            raise ValueError("partial comparison coverage requires reasons")
        return self


class ComparePresentation(StrictModel):
    modes: tuple[ComparePresentationMode, ...] = ("side-by-side", "unified")
    swap: Literal[True] = True
    diff_only: Literal[True] = True


class CompareResourcePair(StrictModel):
    scope: ClusterScope
    descriptor: CompareDescriptor
    coverage: CompareCoverage
    presentation: ComparePresentation
    a: ComparableManifest
    b: ComparableManifest

    @model_validator(mode="after")
    def pair_identity_is_exact_and_distinct(self) -> CompareResourcePair:
        for manifest in (self.a, self.b):
            resource = manifest.resource
            if (
                resource.api_group != self.descriptor.api_group
                or resource.version != self.descriptor.api_version
                or resource.kind != self.descriptor.kubernetes_kind
            ):
                raise ValueError("comparison manifest does not match descriptor identity")
        if self.a.resource == self.b.resource:
            raise ValueError("comparison sides must be distinct resources")
        return self


class CompareCandidate(StrictModel):
    resource: ResourceRef
    provenance: CompareProvenance


class CompareCandidateList(StrictModel):
    scope: ClusterScope
    descriptor: CompareDescriptor
    coverage: CompareCoverage
    candidates: tuple[CompareCandidate, ...] = ()
    excluded_count: int = Field(default=0, ge=0)


class CompareDescriptorListResponse(StrictModel):
    descriptors: tuple[CompareDescriptor, ...]


class CompareResourcePairResponse(StrictModel):
    comparison: CompareResourcePair


class CompareCandidateListResponse(StrictModel):
    result: CompareCandidateList
