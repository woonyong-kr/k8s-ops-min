"""Server-owned browser refresh policies.

The browser may decide *when* a successful response becomes eligible for a
refresh (for example, only while visible), but it must not invent endpoint
cadences.  This contract is the single typed inventory for read refresh
policies that are not already carried by a domain response.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.parity import ClusterScope

RefreshPolicyKey = Literal[
    "dashboard",
    "issues_audit",
    "applications",
    "resource_list",
    "resource_list_slow",
    "changes",
    "metrics_kubernetes",
    "metrics_prometheus",
    "metrics_pvc",
    "metrics_rightsizing",
    "gitops_rows",
    "gitops_counts",
    "helm_list",
    "helm_detail",
    "cost_summary",
    "cost_trend",
    "cost_nodes",
    "port_sessions",
]


class BrowserRefreshPolicy(StrictModel):
    stale_after_seconds: float | None = Field(default=None, gt=0, le=3600)
    refresh_after_seconds: float = Field(gt=0, le=3600)
    keep_last_success: Literal[True] = True
    pause_when_hidden: Literal[True] = True
    event_invalidation: bool = False
    retry_after_seconds: float | None = Field(default=None, gt=0, le=300)
    retry_limit: int | None = Field(default=None, ge=1, le=10)
    post_mutation_refresh_after_seconds: float | None = Field(
        default=None,
        gt=0,
        le=60,
    )

    @model_validator(mode="after")
    def retry_policy_is_complete(self) -> BrowserRefreshPolicy:
        if (self.retry_after_seconds is None) != (self.retry_limit is None):
            raise ValueError("retry_after_seconds and retry_limit must be configured together")
        return self


class BrowserRefreshPoliciesResponse(StrictModel):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    policies: dict[RefreshPolicyKey, BrowserRefreshPolicy]

    @model_validator(mode="after")
    def all_policy_keys_are_present(self) -> BrowserRefreshPoliciesResponse:
        expected = set(RefreshPolicyKey.__args__)
        actual = set(self.policies)
        missing = expected - actual
        if missing:
            raise ValueError(f"refresh policies are missing keys: {sorted(missing)}")
        return self


HomeDashboardEventKind = Literal["connected", "deferred_ready", "heartbeat"]


class HomeDashboardEventFrame(StrictModel):
    """One scope-bound Home invalidation frame carried over authenticated SSE."""

    kind: HomeDashboardEventKind
    cursor: str = Field(min_length=1, max_length=8192)
    scope: ClusterScope
    reconnect_after_ms: int = Field(ge=100, le=30_000)
    snapshot_id: str | None = Field(default=None, min_length=1)
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def snapshot_only_belongs_to_ready_frame(self) -> HomeDashboardEventFrame:
        snapshot_fields = self.snapshot_id is not None, self.occurred_at is not None
        if self.kind == "deferred_ready" and not all(snapshot_fields):
            raise ValueError("deferred_ready requires exactly one durable snapshot position")
        if self.kind != "deferred_ready" and any(snapshot_fields):
            raise ValueError("deferred_ready requires exactly one durable snapshot position")
        return self
