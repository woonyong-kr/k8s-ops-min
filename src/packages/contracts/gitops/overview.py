"""Safe, provider-neutral fleet projection for registered and observed GitOps targets."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.parity import CapabilitySet, ClusterScope, ResourceRef

GitOpsOverviewAuthority = Literal["registered", "controller"]
GitOpsOverviewProvider = Literal["internal", "argo", "flux"]
GitOpsOverviewRole = Literal["controller", "source"]
GitOpsOverviewCoverageState = Literal["complete", "partial", "unavailable"]


class GitOpsOverviewRow(StrictModel):
    id: str = Field(min_length=1)
    authority: GitOpsOverviewAuthority
    provider: GitOpsOverviewProvider
    role: GitOpsOverviewRole
    display_name: str = Field(min_length=1)
    application_ids: tuple[str, ...] = ()
    binding_id: str | None = None
    scope: ClusterScope
    resource: ResourceRef | None = None
    environment: str | None = None
    status: str | None = None
    health: str | None = None
    revision: str | None = None
    observed_at: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    capabilities: CapabilitySet | None = None
    partial_reason_codes: tuple[str, ...] = ()

    @field_validator("application_ids", "partial_reason_codes")
    @classmethod
    def canonicalize_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({value.strip() for value in values if value.strip()}))
        if len(normalized) != len(values):
            raise ValueError("GitOps overview tuple values must be unique and non-empty")
        return normalized

    @model_validator(mode="after")
    def evidence_identity_is_exact(self) -> GitOpsOverviewRow:
        if self.authority == "controller":
            if self.provider == "internal" or self.resource is None or self.capabilities is None:
                raise ValueError(
                    "controller evidence requires provider, resource, and capabilities"
                )
            if self.binding_id is not None:
                raise ValueError("controller evidence cannot manufacture a binding")
        else:
            if self.provider != "internal" or not self.binding_id or not self.application_ids:
                raise ValueError("registered evidence requires an application and binding")
            if self.resource is not None or self.capabilities is not None:
                raise ValueError("registered evidence cannot manufacture a controller resource")
        return self


class GitOpsOverviewKindCount(StrictModel):
    api_group: str
    version: str
    kind: str = Field(min_length=1)
    provider: Literal["argo", "flux"]
    role: GitOpsOverviewRole
    count: int = Field(ge=0)
    completeness: Literal["exact", "partial"]


class GitOpsOverviewCoverage(StrictModel):
    state: GitOpsOverviewCoverageState
    registered_count: int = Field(ge=0)
    controller_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()

    @field_validator("reason_codes")
    @classmethod
    def canonicalize_reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({value.strip() for value in values if value.strip()}))
        if len(normalized) != len(values):
            raise ValueError("coverage reason codes must be unique and non-empty")
        return normalized

    @model_validator(mode="after")
    def state_matches_reasons(self) -> GitOpsOverviewCoverage:
        if self.state == "complete" and self.reason_codes:
            raise ValueError("complete GitOps overview coverage cannot have reasons")
        if self.state != "complete" and not self.reason_codes:
            raise ValueError("non-complete GitOps overview coverage requires reasons")
        return self


class GitOpsOverviewResponse(StrictModel):
    workspace_id: str = Field(min_length=1)
    scopes: tuple[ClusterScope, ...]
    items: tuple[GitOpsOverviewRow, ...]
    kind_counts: tuple[GitOpsOverviewKindCount, ...]
    coverage: GitOpsOverviewCoverage
    observed_at: str | None = None

    @model_validator(mode="after")
    def identities_are_unique(self) -> GitOpsOverviewResponse:
        row_ids = tuple(row.id for row in self.items)
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("GitOps overview row ids must be unique")
        controller_resources = tuple(
            (row.scope.cluster_id, row.resource.uid)
            for row in self.items
            if row.authority == "controller" and row.resource is not None
        )
        if len(controller_resources) != len(set(controller_resources)):
            raise ValueError("GitOps overview controller identities must be unique")
        if self.coverage.returned_count != len(self.items):
            raise ValueError("GitOps overview returned count must match items")
        return self
