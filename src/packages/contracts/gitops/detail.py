"""Provider-neutral GitOps application-detail boundary.

This contract intentionally reports *availability* separately from a GitOps
provider's state.  A source revision or an Opsia workflow record is not proof
of a controller-rendered desired/live diff, so callers must not infer one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from packages.contracts.gateway.base import StrictModel
from packages.contracts.parity import CapabilitySet, ClusterScope, ResourceRef

GitOpsAvailability = Literal["available", "partial", "unavailable"]
GitOpsAuthorization = Literal["allowed", "denied"]
GitOpsAction = Literal["refresh", "sync"]
GitOpsReasonCode = Literal[
    "binding_scope_unavailable",
    "multiple_target_scopes",
    "live_observation_not_integrated",
    "source_revision_unavailable",
    "workflow_operation_unobserved",
    "provider_operation_not_integrated",
    "not_authorized",
    "operation_in_progress",
    "provider_refresh_not_integrated",
    "provider_sync_not_integrated",
]


class GitOpsApplicationScope(StrictModel):
    """The one target scope only when it can be represented unambiguously."""

    availability: GitOpsAvailability
    scope: ClusterScope | None = None
    reason_code: GitOpsReasonCode | None = None

    @model_validator(mode="after")
    def availability_matches_scope(self) -> GitOpsApplicationScope:
        if self.availability == "available" and self.scope is None:
            raise ValueError("available scope requires a concrete ClusterScope")
        if self.availability == "unavailable" and self.scope is not None:
            raise ValueError("unavailable scope cannot claim a ClusterScope")
        if self.availability != "available" and not self.reason_code:
            raise ValueError("non-available scope requires a reason_code")
        return self


class GitOpsSource(StrictModel):
    """Safe source identity; credentials and source contents never cross this boundary."""

    repository_ref: str | None = None
    default_branch: str | None = None
    manifest_path: str | None = None


class GitOpsDesiredLiveDiffAvailability(StrictModel):
    """Revision-bound statement about desired/live comparison availability.

    This P0 carries no diff body.  ``available`` is reserved for a later
    provider integration that can return a separately validated comparison
    artifact.  It may not be inferred from workflow history.
    """

    availability: GitOpsAvailability
    source_revision: str | None = None
    live_observation_revision: str | None = None
    reason_code: GitOpsReasonCode | None = None

    @model_validator(mode="after")
    def availability_has_evidence_or_reason(self) -> GitOpsDesiredLiveDiffAvailability:
        if self.availability == "available":
            raise ValueError("an available desired/live diff requires a comparison artifact")
        if not self.reason_code:
            raise ValueError("non-available desired/live diff requires a reason_code")
        return self


class GitOpsOperationObservation(StrictModel):
    """Observed Opsia workflow state, not an assertion about an external controller."""

    availability: GitOpsAvailability
    in_progress: bool | None = None
    workflow_run_id: str | None = None
    status: str | None = None
    observed_at: str | None = None
    reason_code: GitOpsReasonCode | None = None

    @model_validator(mode="after")
    def operation_state_is_explicit(self) -> GitOpsOperationObservation:
        if self.availability == "unavailable":
            if any(
                (self.in_progress is not None, self.workflow_run_id, self.status, self.observed_at)
            ):
                raise ValueError("unavailable operation cannot claim workflow observation")
        if self.in_progress is True and (not self.workflow_run_id or not self.status):
            raise ValueError("in-progress operation requires the observed workflow identity")
        if self.availability != "available" and not self.reason_code:
            raise ValueError("partial or unavailable operation requires a reason_code")
        return self


class GitOpsActionCapability(StrictModel):
    """One provider action's authorization and actual integration availability."""

    action: GitOpsAction
    authorization: GitOpsAuthorization
    availability: GitOpsAvailability
    # This read-only projection intentionally has no command executor.  Keep
    # the wire contract closed until a CommandRequest/CommandReceipt action
    # endpoint is introduced; clients must never surface a clickable action
    # without that audited execution contract.
    enabled: Literal[False] = False
    operation_blocked: bool = False
    reason_code: GitOpsReasonCode | None = None

    @model_validator(mode="after")
    def enabled_requires_authorized_available_action(self) -> GitOpsActionCapability:
        if self.enabled and (self.authorization != "allowed" or self.availability != "available"):
            raise ValueError("enabled action must be authorized and available")
        if not self.enabled and not self.reason_code:
            raise ValueError("disabled action requires a reason_code")
        if self.operation_blocked and self.action != "sync":
            raise ValueError("only sync may be blocked by a running operation")
        if self.operation_blocked and self.enabled:
            raise ValueError("operation-blocked sync cannot be enabled")
        return self


class GitOpsApplicationDetail(StrictModel):
    application_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    resource: ResourceRef
    scope: GitOpsApplicationScope
    source: GitOpsSource
    desired_live_diff: GitOpsDesiredLiveDiffAvailability
    operation: GitOpsOperationObservation
    capabilities: tuple[GitOpsActionCapability, ...]

    @model_validator(mode="after")
    def has_exactly_one_capability_per_p0_action(self) -> GitOpsApplicationDetail:
        actions = tuple(capability.action for capability in self.capabilities)
        if actions != ("refresh", "sync"):
            raise ValueError("GitOps detail capabilities must be refresh then sync")
        return self


class GitOpsApplicationDetailResponse(StrictModel):
    application: GitOpsApplicationDetail


GitOpsProvider = Literal["argo", "flux"]
GitOpsResourceAction = Literal[
    "reconcile",
    "sync_with_source",
    "suspend",
    "resume",
    "sync",
    "refresh",
]
GitOpsTreeNodeRole = Literal["root", "declared", "generated", "source", "dependency"]
GitOpsTreeRelationship = Literal["owns", "source", "depends_on"]
GitOpsTreeCoverageState = Literal["complete", "partial"]
GitOpsTreeReasonCode = Literal[
    "snapshot_incomplete",
    "snapshot_mismatch",
    "inventory_query_limit",
    "node_limit_reached",
    "edge_limit_reached",
    "unresolved_declared_resource",
    "resource_identity_incomplete",
]


class GitOpsTreeNode(StrictModel):
    id: str = Field(min_length=1)
    resource: ResourceRef
    role: GitOpsTreeNodeRole
    status: str | None = None
    health: str | None = None


class GitOpsTreeEdge(StrictModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    relationship: GitOpsTreeRelationship


class GitOpsTreeCoverage(StrictModel):
    state: GitOpsTreeCoverageState
    reason_codes: tuple[GitOpsTreeReasonCode, ...] = ()
    observed_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)

    @model_validator(mode="after")
    def partial_state_has_reason(self) -> GitOpsTreeCoverage:
        if self.state == "complete" and self.reason_codes:
            raise ValueError("complete GitOps tree coverage cannot have reason codes")
        if self.state == "partial" and not self.reason_codes:
            raise ValueError("partial GitOps tree coverage requires a reason code")
        if self.returned_count > self.observed_count:
            raise ValueError("GitOps tree returned count cannot exceed observed count")
        return self


class GitOpsResourceTreeResponse(StrictModel):
    scope: ClusterScope
    root: ResourceRef
    nodes: tuple[GitOpsTreeNode, ...]
    edges: tuple[GitOpsTreeEdge, ...]
    coverage: GitOpsTreeCoverage


class GitOpsCondition(StrictModel):
    type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    reason: str | None = None
    message: str | None = None
    observed_at: str | None = None


class GitOpsHistoryEntry(StrictModel):
    id: str | None = None
    revision: str | None = None
    deployed_at: str | None = None
    phase: str | None = None
    message: str | None = None
    initiated_by: str | None = None


class GitOpsResourceInsights(StrictModel):
    scope: ClusterScope
    resource: ResourceRef
    resource_version: str = Field(min_length=1)
    provider: GitOpsProvider
    status: str | None = None
    health: str | None = None
    revision: str | None = None
    source: ResourceRef | None = None
    conditions: tuple[GitOpsCondition, ...] = ()
    history: tuple[GitOpsHistoryEntry, ...] = ()
    capabilities: CapabilitySet


class GitOpsResourceInsightsResponse(StrictModel):
    insights: GitOpsResourceInsights


class GitOpsSyncResource(StrictModel):
    api_group: str = ""
    kind: str = Field(min_length=1)
    namespace: str | None = None
    name: str = Field(min_length=1)


class GitOpsSyncOptions(StrictModel):
    revision: str | None = None
    prune: bool = True
    dry_run: bool = False
    force: bool = False
    apply_only: bool = False
    sync_options: tuple[str, ...] = Field(default=(), max_length=100)
    resources: tuple[GitOpsSyncResource, ...] = Field(default=(), max_length=500)

    @field_validator("sync_options")
    @classmethod
    def canonicalize_sync_options(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        if len(normalized) != len(values):
            raise ValueError("sync options must be unique and non-empty")
        return normalized

    @field_validator("resources")
    @classmethod
    def require_unique_resources(
        cls, values: tuple[GitOpsSyncResource, ...]
    ) -> tuple[GitOpsSyncResource, ...]:
        identities = tuple(
            (value.api_group, value.kind.casefold(), value.namespace or "", value.name)
            for value in values
        )
        if len(set(identities)) != len(identities):
            raise ValueError("selective sync resources must be unique")
        return values


class GitOpsResourceActionRequest(StrictModel):
    cluster_id: str = Field(min_length=1)
    resource: ResourceRef
    resource_version: str = Field(min_length=1, max_length=253)
    capability_revision: str = Field(min_length=1)
    action: GitOpsResourceAction
    confirmation: Literal[True]
    reason: str = Field(min_length=1, max_length=500)
    options: GitOpsSyncOptions | None = None
    refresh_mode: Literal["normal", "hard"] | None = None

    @model_validator(mode="after")
    def options_match_action(self) -> GitOpsResourceActionRequest:
        if self.action != "sync" and self.options is not None:
            raise ValueError("GitOps sync options are valid only for sync")
        if self.action == "sync" and self.options is None:
            self.options = GitOpsSyncOptions()
        if self.action != "refresh" and self.refresh_mode is not None:
            raise ValueError("GitOps refresh mode is valid only for refresh")
        if self.action == "refresh" and self.refresh_mode is None:
            self.refresh_mode = "normal"
        return self
